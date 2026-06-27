"""
TopologyPNGExporter — Render a network topology graph as a high-resolution
PNG image using matplotlib + networkx.

Produces 1920×1080 images with colour-coded device types,
dashed borders for virtual devices, and edge-type legends.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx

logger = logging.getLogger(__name__)

# ── Node size mapping ────────────────────────────────────────────────
NODE_SIZES: Dict[str, float] = {
    "self": 800,
    "router": 1000,
    "switch": 900,
    "endpoint": 500,
}

# ── matplotlib marker mapping ────────────────────────────────────────
# NOTE: matplotlib scatter markers for networkx draw:
#   "s" = square, "D" = diamond, "o" = circle
SHAPE_MARKERS: Dict[str, str] = {
    "self": "s",
    "router": "D",
    "switch": "s",
    "endpoint": "o",
}

# ── Edge colour mapping ──────────────────────────────────────────────
EDGE_COLORS: Dict[str, str] = {
    "L2_DIRECT": "#6BCB77",
    "L3_ROUTE": "#FF6B6B",
}


class TopologyPNGExporter:
    """Export an ``nx.Graph`` as a styled PNG image."""

    def __init__(self) -> None:
        self._font_name: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(
        self,
        graph: nx.Graph,
        output_path: str = "output/topology.png",
        title: str = "Network Topology",
        dpi: int = 100,
        figsize: Tuple[float, float] = (19.2, 10.8),
    ) -> str:
        """Render *graph* to a PNG file at *output_path*.

        Returns the absolute path to the saved file.
        """
        if graph.number_of_nodes() == 0:
            logger.warning("TopologyPNGExporter: graph is empty — nothing to render")
            return ""

        # ── Font setup ─────────────────────────────────────────────
        self._setup_fonts()

        # ── Prepare directories ────────────────────────────────────
        out_dir = os.path.dirname(output_path) or "output"
        os.makedirs(out_dir, exist_ok=True)

        # ── Layout ─────────────────────────────────────────────────
        try:
            pos = nx.kamada_kawai_layout(graph)
        except Exception:
            logger.warning("kamada_kawai_layout failed; falling back to spring_layout")
            pos = nx.spring_layout(graph, seed=42)

        # ── Build per-draw-type node lists ─────────────────────────
        groups = self._group_nodes_by_type(graph)
        virtual_nodes = {
            n for n, d in graph.nodes(data=True) if d.get("is_virtual", False)
        }

        # ── Draw ───────────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
        ax.set_aspect("equal")
        ax.axis("off")

        # Edge groups
        l2_edges: List[Tuple] = []
        l3_edges: List[Tuple] = []
        edge_labels: Dict[Tuple, str] = {}

        for u, v, data in graph.edges(data=True):
            etype = data.get("type", "L2_DIRECT")
            if etype == "L3_ROUTE":
                l3_edges.append((u, v))
            else:
                l2_edges.append((u, v))
            edge_labels[(u, v)] = "L3" if etype == "L3_ROUTE" else "L2"

        # Draw L2 edges (green)
        if l2_edges:
            nx.draw_networkx_edges(
                graph,
                pos,
                edgelist=l2_edges,
                edge_color=EDGE_COLORS["L2_DIRECT"],
                width=2.0,
                alpha=0.8,
                ax=ax,
                arrows=False,
            )

        # Draw L3 edges (red)
        if l3_edges:
            nx.draw_networkx_edges(
                graph,
                pos,
                edgelist=l3_edges,
                edge_color=EDGE_COLORS["L3_ROUTE"],
                width=2.5,
                alpha=0.9,
                ax=ax,
                arrows=True,
                arrowstyle="-|>",
                arrowsize=20,
                connectionstyle="arc3,rad=0.1",
            )

        # Edge labels
        if edge_labels:
            nx.draw_networkx_edge_labels(
                graph,
                pos,
                edge_labels=edge_labels,
                font_size=8,
                font_color="#555555",
                ax=ax,
            )

        # Draw nodes by type group (one pass per shape/colour group)
        for dtype, node_list in groups.items():
            if not node_list:
                continue

            marker = SHAPE_MARKERS.get(dtype, "o")
            these_virtual = [n for n in node_list if n in virtual_nodes]
            these_solid = [n for n in node_list if n not in virtual_nodes]

            # Solid-border nodes
            if these_solid:
                nx.draw_networkx_nodes(
                    graph, pos,
                    nodelist=these_solid,
                    node_color=[graph.nodes[n].get("color", "#6BCB77") for n in these_solid],
                    node_shape=marker,
                    node_size=[NODE_SIZES.get(graph.nodes[n].get("type"), 500) for n in these_solid],
                    edgecolors="#333333",
                    linewidths=1.5,
                    ax=ax,
                )

            # Dashed-border (virtual) nodes — draw as rectangles manually
            if these_virtual:
                for n in these_virtual:
                    node_color = graph.nodes[n].get("color", "#6BCB77")
                    node_size = NODE_SIZES.get(graph.nodes[n].get("type"), 500)
                    x, y = pos[n]
                    # Convert node_size to a display-coordinate side length
                    # networkx node_size is area in points^2 → side = sqrt(area) points
                    side_pt = (node_size ** 0.5) * 0.8
                    # Transform to data coordinates: crude but effective for square markers
                    # Use fixed-size in axis fraction space 0.02
                    half = 0.018
                    rect = mpatches.FancyBboxPatch(
                        (x - half, y - half), half * 2, half * 2,
                        boxstyle="round,pad=0.003",
                        facecolor=node_color,
                        edgecolor="#333333",
                        linewidth=2.0,
                        linestyle="dashed",
                        transform=ax.transData,
                    )
                    ax.add_patch(rect)

        # ── Node labels ────────────────────────────────────────────
        labels: Dict[str, str] = {}
        for node, data in graph.nodes(data=True):
            dtype = data.get("type", "endpoint")
            ip = data.get("ip")
            if dtype == "self":
                labels[node] = f"Me\n({ip})" if ip else "Me"
            elif data.get("is_virtual"):
                labels[node] = "Virtual\nSwitch"
            elif dtype == "switch" and ip:
                labels[node] = f"Switch\n{ip}"
            elif dtype == "router" and ip:
                labels[node] = f"Router\n{ip}"
            elif ip:
                labels[node] = ip
            else:
                labels[node] = str(node)

        nx.draw_networkx_labels(
            graph, pos,
            labels=labels,
            font_size=7,
            font_color="#222222",
            ax=ax,
        )

        # ── Title ──────────────────────────────────────────────────
        if self._font_name:
            ax.set_title(title, fontsize=18, fontweight="bold", pad=20,
                         fontfamily=self._font_name)
        else:
            ax.set_title(title, fontsize=18, fontweight="bold", pad=20)

        # ── Legend ─────────────────────────────────────────────────
        legend_patches = [
            mpatches.Patch(color="#4D96FF", label=f"self ({'▨' if 's' == SHAPE_MARKERS.get('self') else '●'})"),
            mpatches.Patch(color="#FF6B6B", label=f"router ({'◆' if 'D' == SHAPE_MARKERS.get('router') else '●'})"),
            mpatches.Patch(color="#FFD93D", label=f"switch ({'■' if 's' == SHAPE_MARKERS.get('switch') else '●'})"),
            mpatches.Patch(color="#6BCB77", label=f"endpoint ({'●' if 'o' == SHAPE_MARKERS.get('endpoint') else '●'})"),
            mpatches.Patch(edgecolor="#333333", facecolor="white", linewidth=2,
                           linestyle="dashed", label="virtual (dashed)"),
            mpatches.Patch(color=EDGE_COLORS["L2_DIRECT"], label="L2 link"),
            mpatches.Patch(color=EDGE_COLORS["L3_ROUTE"], label="L3 route"),
        ]
        ax.legend(
            handles=legend_patches,
            loc="upper left",
            bbox_to_anchor=(1.01, 1),
            fontsize=9,
            frameon=True,
            edgecolor="#cccccc",
        )

        # ── Save ───────────────────────────────────────────────────
        abs_path = os.path.abspath(output_path)
        fig.tight_layout()
        fig.savefig(abs_path, dpi=dpi, bbox_inches="tight",
                    facecolor="white", edgecolor="none")
        plt.close(fig)

        logger.info("TopologyPNGExporter: saved → %s", abs_path)
        return abs_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _group_nodes_by_type(graph: nx.Graph) -> Dict[str, List]:
        """Group node IDs by their ``type`` attribute."""
        groups: Dict[str, List] = {}
        for node, data in graph.nodes(data=True):
            dtype = data.get("type", "endpoint")
            groups.setdefault(dtype, []).append(node)
        return groups

    def _setup_fonts(self) -> None:
        """Detect a suitable font that supports CJK characters.

        Priority: SimHei → WenQuanYi Micro Hei → Noto Sans CJK →
        Arial → DejaVu Sans → matplotlib default.
        """
        import matplotlib.font_manager as fm

        candidates = [
            "SimHei",
            "WenQuanYi Micro Hei",
            "WenQuanYi Zen Hei",
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "Source Han Sans SC",
            "Arial",
            "DejaVu Sans",
        ]

        available = {f.name for f in fm.fontManager.ttflist}

        for name in candidates:
            if name in available:
                self._font_name = name
                matplotlib.rcParams["font.family"] = name
                matplotlib.rcParams["axes.unicode_minus"] = False
                logger.debug("Using font: %s", name)
                return

        # Fallback: accept whatever matplotlib gives us
        logger.debug("No preferred font found; using matplotlib default")
        self._font_name = None


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="TopologyPNGExporter self-test")
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
    parser.add_argument(
        "--output", "-o", default="output/topology.png", help="Output PNG path"
    )
    args = parser.parse_args()

    from scanner.ping import PingScanner
    from scanner.arp import ARPReader
    from scanner.traceroute import TracerouteScanner
    from topology.classifier import DeviceClassifier
    from topology.linker import LinkInferrer
    from topology.graph_builder import GraphBuilder

    print("=" * 60)
    print("  TopologyPNGExporter — Self-test")
    print("=" * 60)

    # ── Scan pipeline ─────────────────────────────────────────────
    print("\n[1/5] Ping scan ...")
    ping = PingScanner(timeout=1.0, concurrency=30)
    online = ping.scan_subnet(args.subnet)
    print(f"  Online: {len(online)}")

    print("\n[2/5] ARP cache ...")
    arp_reader = ARPReader()
    arp = arp_reader.read_cache(interface=args.interface)
    print(f"  Entries: {len(arp)}")

    print("\n[3/5] Traceroute ...")
    tr_targets = ["8.8.8.8", "114.114.114.114", "1.1.1.1"]
    tr = TracerouteScanner(max_hops=15, timeout=3.0, targets=tr_targets)
    tr_results = tr.trace_all()

    print("\n[4/5] Classify + Link + Build ...")
    classifier = DeviceClassifier()
    devices = classifier.classify(
        online_ips=online, gateway=args.gateway, arp_entries=arp,
        local_ip=args.local_ip, traceroute_results=tr_results,
        traceroute_targets=tr_targets,
    )

    linker = LinkInferrer()
    links = linker.infer(devices, args.subnet, args.gateway, tr_results)

    builder = GraphBuilder()
    graph = builder.build(devices, links)
    print(f"  Nodes: {graph.number_of_nodes()}, Edges: {graph.number_of_edges()}")

    # ── Export PNG ────────────────────────────────────────────────
    print("\n[5/5] Rendering & saving PNG ...")
    exporter = TopologyPNGExporter()
    result = exporter.export(graph, output_path=args.output,
                             title="Network Topology — Self-test")
    if result:
        file_size = os.path.getsize(result)
        print(f"  ✅ PNG saved: {result}")
        print(f"  File size: {file_size:,} bytes ({file_size / 1024:.1f} KB)")
    else:
        print("  ⚠️  No output (empty graph?)")
