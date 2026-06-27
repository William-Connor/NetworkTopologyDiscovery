"""
LinkInferrer — Infer L2 / L3 links between classified devices.

Input:  classified device list from DeviceClassifier
Output: list of link dicts (source, target, type, hops, updated).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Virtual switch identifier used as node id in links
VIRTUAL_SWITCH_ID = "VirtualSwitch"


class LinkInferrer:
    """Infer topology links from a classified device list."""

    def infer(
        self,
        devices: List[dict],
        subnet: str,
        gateway: str,
        traceroute_results: Optional[Dict[str, List[dict]]] = None,
    ) -> List[dict]:
        """Return a list of inferred links.

        Each link::

            {
                "source": str,
                "target": str,
                "type": "L2_DIRECT" | "L3_ROUTE",
                "hops": int,
                "updated": "ISO-8601-timestamp",
            }
        """
        now = datetime.now(timezone.utc).isoformat()

        # ── Index devices by type ─────────────────────────────────────
        endpoints: List[dict] = []
        switches: List[dict] = []
        routers: List[dict] = []

        for d in devices:
            dtype = d["type"]
            if dtype in ("endpoint", "self"):
                endpoints.append(d)
            elif dtype == "switch":
                switches.append(d)
            elif dtype == "router":
                routers.append(d)

        # ── Pick the primary switch ───────────────────────────────────
        primary_switch_id = self._pick_switch_id(switches)

        links: List[dict] = []

        # ── 1.  Local endpoints → Switch  (L2_DIRECT) ──────────────
        # ──    Remote endpoints → Gateway  (L3_ROUTE) ───────────────
        for ep in endpoints:
            if ep.get("is_remote"):
                # Remote endpoint: cross-subnet L3 link to gateway
                links.append(
                    {
                        "source": ep["ip"],
                        "target": gateway,
                        "type": "L3_ROUTE",
                        "hops": 2,
                        "updated": now,
                    }
                )
            else:
                # Local endpoint: L2 link to switch
                links.append(
                    {
                        "source": ep["ip"],
                        "target": primary_switch_id,
                        "type": "L2_DIRECT",
                        "hops": 1,
                        "updated": now,
                    }
                )

        # ── 2.  Switch → Gateway  (L2_DIRECT) ─────────────────────────
        links.append(
            {
                "source": primary_switch_id,
                "target": gateway,
                "type": "L2_DIRECT",
                "hops": 1,
                "updated": now,
            }
        )

        # ── 3.  Gateway → Upstream routers  (L3_ROUTE) ────────────────
        # Compute upstream hop counts from traceroute (minimum TTL per IP)
        upstream_hops: Dict[str, int] = {}
        if traceroute_results:
            for _target, hops in traceroute_results.items():
                for hop in hops:
                    if hop["ip"] and hop["ip"] != gateway:
                        upstream_hops[hop["ip"]] = min(
                            upstream_hops.get(hop["ip"], 999),
                            hop["ttl"],
                        )

        upstream_routers = [
            r for r in routers if r["ip"] != gateway and r["ip"] is not None
        ]
        for ur in upstream_routers:
            hops = upstream_hops.get(ur["ip"], 1)
            links.append(
                {
                    "source": gateway,
                    "target": ur["ip"],
                    "type": "L3_ROUTE",
                    "hops": hops,
                    "updated": now,
                }
            )

        logger.info(
            "LinkInferrer: %d links (%d L2, %d L3)",
            len(links),
            sum(1 for lk in links if lk["type"] == "L2_DIRECT"),
            sum(1 for lk in links if lk["type"] == "L3_ROUTE"),
        )
        return links

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_switch_id(switches: List[dict]) -> str:
        """Choose the switch node identifier for link endpoints.

        Prefers the first real switch; falls back to the virtual one.
        """
        # Sort real switches before virtual ones
        real = [s for s in switches if not s["is_virtual"]]
        if real:
            switch = real[0]
            # Use IP if available, otherwise VirtualSwitch
            return switch["ip"] or VIRTUAL_SWITCH_ID

        virtual = [s for s in switches if s["is_virtual"]]
        if virtual:
            return VIRTUAL_SWITCH_ID

        # Belt-and-suspenders: create a fallback id
        return VIRTUAL_SWITCH_ID


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="LinkInferrer self-test")
    parser.add_argument(
        "--subnet", default="10.15.117.0/24", help="Subnet"
    )
    parser.add_argument(
        "--gateway", default="10.15.117.1", help="Gateway IP"
    )
    parser.add_argument(
        "--local-ip", default="10.15.117.85", help="Local machine IP"
    )
    parser.add_argument(
        "--interface", "-i", default="eno2", help="ARP interface"
    )
    args = parser.parse_args()

    from scanner.ping import PingScanner
    from scanner.arp import ARPReader
    from scanner.traceroute import TracerouteScanner
    from topology.classifier import DeviceClassifier

    print("=" * 60)
    print("  LinkInferrer — Self-test")
    print("=" * 60)

    # Gather scanner data
    print("\n[1/4] Running PingScanner ...")
    ping = PingScanner(timeout=1.0, concurrency=30)
    online = ping.scan_subnet(args.subnet)
    print(f"  Online hosts: {len(online)}")

    print("\n[2/4] Reading ARP cache ...")
    arp_reader = ARPReader()
    arp = arp_reader.read_cache(interface=args.interface)
    print(f"  ARP entries: {len(arp)}")

    print("\n[3/4] Running TracerouteScanner ...")
    tr_targets = ["8.8.8.8", "114.114.114.114", "1.1.1.1"]
    tr = TracerouteScanner(max_hops=15, timeout=3.0, targets=tr_targets)
    tr_results = tr.trace_all()

    # Classify
    print("\n[4/4] Classifying & linking ...")
    classifier = DeviceClassifier()
    devices = classifier.classify(
        online_ips=online,
        gateway=args.gateway,
        arp_entries=arp,
        local_ip=args.local_ip,
        traceroute_results=tr_results,
        traceroute_targets=tr_targets,
    )

    linker = LinkInferrer()
    links = linker.infer(devices, args.subnet, args.gateway, tr_results)

    print(f"\nInferred links ({len(links)}):")
    print(f"{'Source':<20} {'Target':<20} {'Type':<12} Hops  Updated")
    print("-" * 85)
    for lk in links:
        print(
            f"{lk['source']:<20} {lk['target']:<20} "
            f"{lk['type']:<12} {lk['hops']:<5} {lk['updated']}"
        )
