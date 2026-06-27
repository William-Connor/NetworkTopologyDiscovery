#!/usr/bin/env python3
"""
main.py — FastAPI entry point for Network Topology Discovery.

Serves:
  - Static dashboard (/)
  - REST API (/api/status, /api/topology, /api/export/png, /api/export/xlsx)
  - WebSocket (/ws) for live topology updates
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import uvicorn
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

# Ensure project root is on sys.path for imports
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scanner.network_detect import detect_network, get_local_mac
from scanner.scheduler import ScanScheduler
from topology.graph_builder import GraphBuilder
from export.png import TopologyPNGExporter
from export.excel import LinkExcelExporter

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Network Topology Discovery", version="1.0.0")

# ---------------------------------------------------------------------------
# Templates & static files
# ---------------------------------------------------------------------------
STATIC_DIR = str(PROJECT_ROOT / "web" / "static")

# Mount /static only if not already mounted via app.mount
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# WebSocket Manager
# ---------------------------------------------------------------------------
class WebSocketManager:
    """Broadcast messages to all connected WebSocket clients."""

    def __init__(self) -> None:
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.append(ws)
        logger.info("WS client connected (%d total)", len(self.connections))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.connections:
            self.connections.remove(ws)
        logger.info("WS client disconnected (%d remaining)", len(self.connections))

    async def broadcast(self, message: str) -> None:
        dead: List[WebSocket] = []
        for ws in self.connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)


# ---------------------------------------------------------------------------
# Application state (set during startup)
# ---------------------------------------------------------------------------
scheduler: ScanScheduler | None = None
graph_builder: GraphBuilder | None = None
ws_manager: WebSocketManager | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Render the dashboard page."""
    index_path = PROJECT_ROOT / "web" / "templates" / "index.html"
    return HTMLResponse(content=index_path.read_text())


@app.get("/api/status")
async def api_status() -> dict:
    """Return scanner state + graph statistics."""
    if scheduler is None:
        return {"error": "scheduler not initialized"}
    state = scheduler.get_state()
    state["scan_depth"] = scheduler.scan_depth
    state["additional_subnets"] = scheduler.additional_subnets
    gb = graph_builder
    if gb is not None and gb.graph.number_of_nodes() > 0:
        state["graph"] = {
            "nodes": gb.graph.number_of_nodes(),
            "edges": gb.graph.number_of_edges(),
        }
    else:
        state["graph"] = {"nodes": 0, "edges": 0}
    return state


@app.get("/api/topology")
async def api_topology() -> dict:
    """Return the current topology as vis-network JSON."""
    if graph_builder is None or graph_builder.graph.number_of_nodes() == 0:
        return {"nodes": [], "edges": []}
    return graph_builder.export_json()


@app.get("/api/export/png")
async def api_export_png() -> Response:
    """Generate and return a PNG topology image."""
    if graph_builder is None or graph_builder.graph.number_of_nodes() == 0:
        return Response(content="No topology data yet", status_code=404)

    exporter = TopologyPNGExporter()
    out_path = str(PROJECT_ROOT / "output" / "topology.png")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    exporter.export(
        graph=graph_builder.graph,
        output_path=out_path,
        title="Network Topology",
        dpi=100,
        figsize=(19.2, 10.8),
    )

    return FileResponse(
        out_path,
        media_type="image/png",
        filename="topology.png",
    )


@app.get("/api/export/xlsx")
async def api_export_xlsx() -> Response:
    """Generate and return an Excel topology report."""
    if scheduler is None:
        return Response(content="Scheduler not initialized", status_code=404)

    devices = scheduler.get_last_devices()
    links = scheduler.get_last_links()

    if not devices:
        return Response(content="No topology data yet", status_code=404)

    exporter = LinkExcelExporter()
    out_path = str(PROJECT_ROOT / "output" / "links.xlsx")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    exporter.export(links=links, devices=devices, output_path=out_path)

    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="links.xlsx",
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if ws_manager is None:
        await websocket.close()
        return

    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")

            if action == "pause":
                if scheduler:
                    scheduler.stop()
            elif action == "resume":
                if scheduler:
                    scheduler.start()
            elif action == "get_topology":
                if graph_builder is not None:
                    await websocket.send_json({
                        "type": "topology_update",
                        "data": graph_builder.export_json(),
                    })
            elif action == "update_config":
                config_update = msg.get("config", {})
                if scheduler:
                    scheduler.update_config(config_update)
                    await websocket.send_json({
                        "type": "config_updated",
                        "config": {
                            "scan_depth": scheduler.scan_depth,
                            "additional_subnets": scheduler.additional_subnets,
                        },
                    })
            elif action == "scan_now":
                if scheduler:
                    if scheduler._manual_scan_in_progress:
                        await websocket.send_json({
                            "type": "info",
                            "message": "Manual scan already in progress — skipping"
                        })
                    else:
                        scheduler._manual_scan_in_progress = True
                        import threading

                        def _manual_scan():
                            try:
                                scheduler._scan_cycle()
                            finally:
                                scheduler._manual_scan_in_progress = False

                        threading.Thread(target=_manual_scan, daemon=True).start()
            elif action == "get_config":
                await websocket.send_json({
                    "type": "config",
                    "data": {
                        "scan_depth": scheduler.scan_depth if scheduler else 1,
                        "additional_subnets": scheduler.additional_subnets if scheduler else [],
                    },
                })
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def load_config(config_path: str = "config.yaml") -> dict:
    """Load YAML configuration file."""
    path = Path(config_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / config_path
    with open(path) as fh:
        return yaml.safe_load(fh)


def startup() -> None:
    """Initialise all components and start the scan scheduler."""
    global scheduler, graph_builder, ws_manager

    # 1. Load config (with fallback defaults)
    try:
        config = load_config()
        logger.info("Config loaded from file")
    except Exception:
        logger.warning("config.yaml not found or invalid — using defaults")
        config = {
            "network": {"interface": "auto", "ping_timeout": 1.0, "ping_concurrency": 30},
            "traceroute": {"max_hops": 30, "timeout": 3.0, "targets": ["8.8.8.8", "114.114.114.114", "1.1.1.1"]},
            "scheduler": {"interval": 60, "offline_threshold": 5},
            "server": {"host": "0.0.0.0", "port": 8080},
            "theme": {"default": "dark"},
            "export": {"png_dpi": 150, "png_width": 1920, "png_height": 1080},
            "logging": {"file": "logs/events.jsonl", "level": "INFO"},
        }

    # 2. Detect network
    net_cfg = config.get("network", {})
    interface = net_cfg.get("interface", "auto")
    net = detect_network(interface=interface)

    # Override config with detected values (except if explicitly set)
    if interface == "auto" or not net_cfg.get("subnet"):
        config.setdefault("network", {})["subnet"] = net["subnet"]
    config.setdefault("network", {})["interface"] = net["interface"]

    logger.info(
        "Network detected: iface=%s ip=%s subnet=%s gateway=%s",
        net["interface"], net["ip"], net["subnet"], net["gateway"],
    )

    # 3. Create scanner
    scheduler = ScanScheduler(config)
    scheduler.local_ip = net["ip"]
    scheduler.gateway = net["gateway"]
    scheduler.local_mac = get_local_mac(net["interface"])
    logger.info("ScanScheduler created (interval=%ds, local_mac=%s)", scheduler.interval, scheduler.local_mac)

    # 4. Create topology graph builder (persistent for diffs)
    graph_builder = GraphBuilder()
    scheduler.set_graph_builder(graph_builder)

    # 5. Create WebSocket manager and thread-safe broadcast wrapper
    ws_manager = WebSocketManager()
    _event_loop = asyncio.get_event_loop()

    def ws_broadcast_sync(message: str) -> None:
        """Thread-safe synchronous WS broadcast."""
        if ws_manager is not None:
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast(message), _event_loop
            )

    scheduler.set_ws_manager(ws_manager, broadcast_fn=ws_broadcast_sync)

    # 6. Start scanning
    scheduler.start()
    logger.info("ScanScheduler started")


@app.on_event("startup")
async def on_startup() -> None:
    """FastAPI startup event — run initialisation."""
    startup()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Network Topology Discovery Server")
    parser.add_argument("--host", default="0.0.0.0", help="Listen address")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    # Load config for server settings
    try:
        config = load_config()
        server_cfg = config.get("server", {})
    except Exception:
        server_cfg = {}

    host = server_cfg.get("host", args.host)
    port = server_cfg.get("port", args.port)

    logger.info("Starting uvicorn on %s:%d", host, port)
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )
