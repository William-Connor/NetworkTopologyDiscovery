"""
GraphBuilder — Build a networkx graph from devices and links,
export vis-network JSON, and track diffs between scans.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx

logger = logging.getLogger(__name__)

# ── Shape constants ────────────────────────────────────────────────────
SHAPE_SELF = "star"
SHAPE_ROUTER = "diamond"
SHAPE_SWITCH = "square"
SHAPE_ENDPOINT = "dot"

# ── Edge visual constants ─────────────────────────────────────────────
EDGE_COLOR_L2 = "#6BCB77"
EDGE_COLOR_L3 = "#FF6B6B"

_SHAPE_MAP = {
    "self": SHAPE_SELF,
    "router": SHAPE_ROUTER,
    "switch": SHAPE_SWITCH,
    "endpoint": SHAPE_ENDPOINT,
}


class GraphBuilder:
    """Build and maintain a networkx Graph of the discovered topology."""

    def __init__(self) -> None:
        self._graph: nx.Graph = nx.Graph()
        self._previous_devices: List[dict] = []
        self._previous_links: List[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, devices: List[dict], links: List[dict]) -> nx.Graph:
        """Rebuild the graph from *devices* and *links*.

        Returns the new ``nx.Graph``.
        """
        self._previous_devices = list(self._graph.nodes(data=True))
        self._previous_links = list(self._graph.edges(data=True))

        g = nx.Graph()

        # ── Add nodes (batch) ───────────────────────────────────────
        g.add_nodes_from(
            (
                self._node_id(d),
                {
                    "type": d["type"],
                    "ip": d["ip"],
                    "mac": d["mac"],
                    "vendor": d.get("vendor", "Unknown"),
                    "status": d["status"],
                    "color": d["color"],
                    "is_virtual": d["is_virtual"],
                    "is_remote": d.get("is_remote", False),
                },
            )
            for d in devices
        )

        # ── Add edges (batch) ───────────────────────────────────────
        g.add_edges_from(
            (
                lk["source"],
                lk["target"],
                {"type": lk["type"], "hops": lk["hops"]},
            )
            for lk in links
        )

        self._graph = g
        logger.info("GraphBuilder: %d nodes, %d edges", g.number_of_nodes(), g.number_of_edges())
        return g

    def export_json(self) -> dict:
        """Export the current graph in vis-network compatible format.

        Returns a dict with ``"nodes"`` and ``"edges"`` keys.
        """
        nodes: List[dict] = []
        for node_id, attrs in self._graph.nodes(data=True):
            nodes.append(
                {
                    "id": str(node_id),
                    "label": self._node_label(node_id, attrs),
                    "group": attrs.get("type", "endpoint"),
                    "color": attrs.get("color", "#6BCB77"),
                    "shape": _SHAPE_MAP.get(attrs.get("type"), "dot"),
                    "ip": attrs.get("ip"),
                    "mac": attrs.get("mac"),
                    "vendor": attrs.get("vendor", ""),
                    "is_virtual": attrs.get("is_virtual", False),
                    "is_remote": attrs.get("is_remote", False),
                }
            )

        edges: List[dict] = []
        for u, v, attrs in self._graph.edges(data=True):
            etype = attrs.get("type", "L2_DIRECT")
            is_l3 = etype == "L3_ROUTE"
            edges.append(
                {
                    "from": str(u),
                    "to": str(v),
                    "arrows": "to" if is_l3 else "none",
                    "label": "L3" if is_l3 else "L2",
                    "color": {"color": EDGE_COLOR_L3 if is_l3 else EDGE_COLOR_L2},
                }
            )

        logger.info("export_json: %d nodes, %d edges", len(nodes), len(edges))
        return {"nodes": nodes, "edges": edges}

    def diff(self) -> dict:
        """Compare current graph against the previous scan.

        Returns::

            {
                "added_nodes": [...],
                "removed_nodes": [...],
                "added_links": [...],
                "removed_links": [...],
            }
        """
        prev_nodes: Set[str] = {str(n) for n, _ in self._previous_devices}
        prev_edges: Set[Tuple[str, str]] = {
            (str(u), str(v)) for u, v, _ in self._previous_links
        }
        prev_edges.update(
            {(str(v), str(u)) for u, v, _ in self._previous_links}
        )

        curr_nodes: Set[str] = {str(n) for n in self._graph.nodes()}
        curr_edges: Set[Tuple[str, str]] = {
            (str(u), str(v)) for u, v in self._graph.edges()
        }

        result = {
            "added_nodes": sorted(curr_nodes - prev_nodes),
            "removed_nodes": sorted(prev_nodes - curr_nodes),
            "added_links": sorted(
                (u, v) for u, v in curr_edges if (u, v) not in prev_edges
            ),
            "removed_links": sorted(
                (u, v) for u, v in prev_edges if (u, v) not in curr_edges
            ),
        }
        logger.info(
            "diff: +%d/-%d nodes, +%d/-%d links",
            len(result["added_nodes"]),
            len(result["removed_nodes"]),
            len(result["added_links"]),
            len(result["removed_links"]),
        )
        return result

    @property
    def graph(self) -> nx.Graph:
        """Access the underlying networkx Graph."""
        return self._graph

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_id(device: dict) -> str:
        """Derive a stable node id from a device dict."""
        ip = device.get("ip")
        if ip is not None:
            return ip
        if device.get("is_virtual"):
            return "VirtualSwitch"
        # Fallback: use MAC
        mac = device.get("mac")
        if mac:
            return mac
        return "unknown"

    @staticmethod
    def _node_label(node_id: str, attrs: dict) -> str:
        """Produce a human-readable label for vis-network."""
        ip = attrs.get("ip")
        dtype = attrs.get("type", "endpoint")
        if dtype == "switch" and attrs.get("is_virtual"):
            return "Switch (virtual)"
        if dtype == "switch":
            return f"Switch ({ip})" if ip else "Switch"
        if dtype == "self":
            return f"🖥️ Me ({ip})"
        if ip:
            return ip
        return str(node_id)


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="GraphBuilder self-test")
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
    from topology.classifier import DeviceClassifier
    from topology.linker import LinkInferrer

    print("=" * 60)
    print("  GraphBuilder — Self-test")
    print("=" * 60)

    # ── Gather scanner data ────────────────────────────────────────
    print("\n[1/5] Running PingScanner ...")
    ping = PingScanner(timeout=1.0, concurrency=30)
    online = ping.scan_subnet(args.subnet)
    print(f"  Online hosts: {len(online)}")

    print("\n[2/5] Reading ARP cache ...")
    arp_reader = ARPReader()
    arp = arp_reader.read_cache(interface=args.interface)
    print(f"  ARP entries: {len(arp)}")

    print("\n[3/5] Running TracerouteScanner ...")
    tr_targets = ["8.8.8.8", "114.114.114.114", "1.1.1.1"]
    tr = TracerouteScanner(max_hops=15, timeout=3.0, targets=tr_targets)
    tr_results = tr.trace_all()

    # ── Classify ───────────────────────────────────────────────────
    print("\n[4/5] Classifying ...")
    classifier = DeviceClassifier()
    devices = classifier.classify(
        online_ips=online,
        gateway=args.gateway,
        arp_entries=arp,
        local_ip=args.local_ip,
        traceroute_results=tr_results,
        traceroute_targets=tr_targets,
    )

    # ── Link ───────────────────────────────────────────────────────
    print("\n[5/5] Linking & building graph ...")
    linker = LinkInferrer()
    links = linker.infer(devices, args.subnet, args.gateway, tr_results)

    # ── Build & export ─────────────────────────────────────────────
    builder = GraphBuilder()
    graph = builder.build(devices, links)

    print(f"\n  Nodes: {graph.number_of_nodes()}")
    print(f"  Edges: {graph.number_of_edges()}")

    vis_json = builder.export_json()
    print(f"\n  vis-network nodes: {len(vis_json['nodes'])}")
    print(f"  vis-network edges: {len(vis_json['edges'])}")

    # ── Diff (first run → all added) ───────────────────────────────
    diff_result = builder.diff()
    print(f"\n  Diff: +{len(diff_result['added_nodes'])}/-{len(diff_result['removed_nodes'])} nodes, "
          f"+{len(diff_result['added_links'])}/-{len(diff_result['removed_links'])} links")

    # ── Print compact topology summary ─────────────────────────────
    print("\n  Nodes:")
    for n in vis_json["nodes"]:
        print(f"    [{n['group']:<9}] {n['label']:<22} {n['shape']:<8} {n['color']}")

    print("\n  Edges:")
    for e in vis_json["edges"]:
        arr = e.get("arrows", "none")
        print(f"    {e['from']:<18} → {e['to']:<18} [{e['label']}] arrows={arr}")

    # ── Write vis-network JSON to stdout (first 2000 chars) ───────
    print("\n  vis-network JSON preview (first 2000 chars):")
    payload = json.dumps(vis_json, indent=2, ensure_ascii=False)
    print(payload[:2000])
    if len(payload) > 2000:
        print(f"  ... ({len(payload) - 2000} more chars)")
