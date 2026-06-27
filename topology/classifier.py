"""
DeviceClassifier — Classify discovered devices into self / router / switch / endpoint.

Consumes output from PingScanner, ARPReader, and TracerouteScanner.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Colour palette (per REQUIREMENTS.md §5) ──────────────────────────
COLOR_SELF = "#4D96FF"
COLOR_ROUTER = "#FF6B6B"
COLOR_SWITCH = "#FFD93D"
COLOR_ENDPOINT = "#6BCB77"
COLOR_REMOTE_ENDPOINT = "#C9B1FF"  # remote subnet terminals


class DeviceClassifier:
    """Classify every known IP into a device type and output a uniform dict."""

    def classify(
        self,
        online_ips: List[str],
        gateway: str,
        arp_entries: List[dict],
        local_ip: str,
        traceroute_results: Dict[str, List[dict]],
        traceroute_targets: Optional[List[str]] = None,
        subnet: Optional[str] = None,
    ) -> List[dict]:
        """Return a list of classified device dicts.

        Each dict::

            {
                "ip": str | None,
                "mac": str | None,
                "vendor": str,
                "type": "self" | "router" | "switch" | "endpoint",
                "status": "online" | "offline",
                "is_virtual": bool,
                "color": str,
            }
        """
        online_set: Set[str] = set(online_ips)

        # ── 1.  Build an ARP lookup {ip → entry} ──────────────────────
        arp_by_ip: Dict[str, dict] = {}
        mac_to_ips: Dict[str, Set[str]] = defaultdict(set)
        for e in arp_entries:
            ip = e["ip"]
            arp_by_ip[ip] = e
            mac_to_ips[e["mac"]].add(ip)

        # ── 2.  Switch detection: one MAC → multiple IPs ──────────────
        switch_macs: Set[str] = {
            mac for mac, ips in mac_to_ips.items() if len(ips) > 1
        }
        # All IPs that belong to a multi-IP MAC are switch IPs,
        # UNLESS one of those IPs is the gateway (then it's a router).
        switch_ips: Set[str] = set()
        for mac in switch_macs:
            ips = mac_to_ips[mac]
            if gateway in ips:
                # Multi-IP MAC that includes gateway → router (not switch)
                # These IPs will be caught by the gateway/router check below
                continue
            switch_ips.update(ips)

        # ── 3.  Traceroute intermediate routers ──────────────────────
        traceroute_routers: Set[str] = set()
        for _target, hops in traceroute_results.items():
            for hop in hops:
                if hop["ip"] is not None:
                    traceroute_routers.add(hop["ip"])

        # Exclude traceroute targets (external IPs like 8.8.8.8, not internal routers)
        if traceroute_targets:
            traceroute_routers.difference_update(traceroute_targets)

        # ── 4.  Collect all known IPs ────────────────────────────────
        all_ips: Set[str] = set()
        all_ips.update(online_set)
        all_ips.update(arp_by_ip.keys())
        all_ips.update(traceroute_routers)
        # Ensure gateway and local_ip are covered even if offline
        all_ips.add(gateway)
        all_ips.add(local_ip)

        devices: List[dict] = []
        has_real_switch = False

        for ip in sorted(all_ips, key=_ip_sort_key):
            entry = arp_by_ip.get(ip, {})
            mac = entry.get("mac")
            vendor = entry.get("vendor", "Unknown")
            is_online = ip in online_set

            # ── Classification cascade ────────────────────────────────
            if ip == local_ip:
                dtype = "self"
                color = COLOR_SELF
            elif ip in switch_ips:
                dtype = "switch"
                color = COLOR_SWITCH
                has_real_switch = True
            elif ip == gateway or ip in traceroute_routers:
                dtype = "router"
                color = COLOR_ROUTER
            else:
                dtype = "endpoint"
                color = COLOR_ENDPOINT

            devices.append(
                {
                    "ip": ip,
                    "mac": mac,
                    "vendor": vendor,
                    "type": dtype,
                    "status": "online" if is_online else "offline",
                    "is_virtual": False,
                    "is_remote": False,
                    "color": color,
                }
            )

        # ── 5.  Virtual switch fallback ───────────────────────────────
        if not has_real_switch:
            logger.info("No real switch detected; adding virtual switch node")
            devices.append(
                {
                    "ip": None,
                    "mac": None,
                    "vendor": "Virtual",
                    "type": "switch",
                    "status": "online",
                    "is_virtual": True,
                    "is_remote": False,
                    "color": COLOR_SWITCH,
                }
            )

        logger.info(
            "DeviceClassifier: %d devices (%d self, %d router, %d switch, %d endpoint)",
            len(devices),
            sum(1 for d in devices if d["type"] == "self"),
            sum(1 for d in devices if d["type"] == "router"),
            sum(1 for d in devices if d["type"] == "switch"),
            sum(1 for d in devices if d["type"] == "endpoint"),
        )
        return devices

    def classify_remote(
        self,
        online_ips: List[str],
        subnet: str,
        gateway: str,
        traceroute_results: Optional[Dict[str, List[dict]]] = None,
        traceroute_targets: Optional[List[str]] = None,
    ) -> List[dict]:
        """Classify devices from a remote subnet (no ARP data available).

        Remote endpoints are marked ``is_remote=True`` and coloured
        ``COLOR_REMOTE_ENDPOINT`` (lavender). Routers discovered via
        traceroute retain their router colour.
        """
        online_set: Set[str] = set(online_ips)

        # Collect traceroute routers
        traceroute_routers: Set[str] = set()
        if traceroute_results:
            for _target, hops in traceroute_results.items():
                for hop in hops:
                    if hop["ip"] is not None:
                        traceroute_routers.add(hop["ip"])
        if traceroute_targets:
            traceroute_routers.difference_update(traceroute_targets)

        devices: List[dict] = []
        all_ips: Set[str] = set(online_set) | traceroute_routers

        for ip in sorted(all_ips, key=_ip_sort_key):
            is_online = ip in online_set

            if ip == gateway or ip in traceroute_routers:
                dtype = "router"
                color = COLOR_ROUTER
            else:
                dtype = "endpoint"
                color = COLOR_REMOTE_ENDPOINT

            devices.append(
                {
                    "ip": ip,
                    "mac": None,          # no ARP table for remote subnets
                    "vendor": "Unknown",
                    "type": dtype,
                    "status": "online" if is_online else "offline",
                    "is_virtual": False,
                    "is_remote": True,
                    "color": color,
                }
            )

        logger.info(
            "DeviceClassifier (remote): subnet=%s → %d devices",
            subnet, len(devices),
        )
        return devices


# ── helpers ────────────────────────────────────────────────────────────

def _ip_sort_key(ip: str) -> tuple:
    """Sort IPs numerically."""
    try:
        parts = ip.split(".")
        return tuple(int(p) for p in parts)
    except (ValueError, AttributeError):
        return (999, 999, 999, 999)


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="DeviceClassifier self-test")
    parser.add_argument(
        "--subnet", default="10.15.117.0/24", help="Subnet to scan"
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

    print("=" * 60)
    print("  DeviceClassifier — Self-test")
    print("=" * 60)

    # 1. Ping scan
    print("\n[1/3] Running PingScanner ...")
    ping = PingScanner(timeout=1.0, concurrency=30)
    online = ping.scan_subnet(args.subnet)
    print(f"  Online hosts: {len(online)}")

    # 2. ARP
    print("\n[2/3] Reading ARP cache ...")
    arp_reader = ARPReader()
    arp = arp_reader.read_cache(interface=args.interface)
    print(f"  ARP entries: {len(arp)}")

    # 3. Traceroute
    print("\n[3/3] Running TracerouteScanner ...")
    tr_targets = ["8.8.8.8", "114.114.114.114", "1.1.1.1"]
    tr = TracerouteScanner(max_hops=15, timeout=3.0, targets=tr_targets)
    tr_results = tr.trace_all()
    for tgt, hops in tr_results.items():
        reached = sum(1 for h in hops if h["ip"])
        print(f"  {tgt}: {reached} hops with reply")

    # Classify
    print("\n" + "=" * 60)
    print("  Classification Results")
    print("=" * 60)

    classifier = DeviceClassifier()
    devices = classifier.classify(
        online_ips=online,
        gateway=args.gateway,
        arp_entries=arp,
        local_ip=args.local_ip,
        traceroute_results=tr_results,
        traceroute_targets=tr_targets,
    )

    print(f"\n{'IP':<16} {'MAC':<18} {'Vendor':<24} {'Type':<10} {'Status':<8} Virtual")
    print("-" * 95)
    for d in devices:
        ip_s = d["ip"] or "(virtual)"
        mac_s = d["mac"] or "—"
        print(
            f"{ip_s:<16} {mac_s:<18} {d['vendor']:<24} "
            f"{d['type']:<10} {d['status']:<8} {d['is_virtual']}"
        )

    print(f"\nTotal: {len(devices)} devices")
