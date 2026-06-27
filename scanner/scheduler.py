"""
ScanScheduler — Orchestrate periodic network scans.

Runs ping → ARP → traceroute cycles on a background thread, tracks
device online/offline state changes, and emits JSON-Lines event logs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .arp import ARPReader
from .ping import PingScanner
from .traceroute import TracerouteScanner

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
EventCallback = Callable[[Dict[str, Any]], None]


class ScanScheduler:
    """Periodic network scanner with device-state tracking."""

    def __init__(self, config: dict) -> None:
        sched_cfg = config.get("scheduler", {})
        net_cfg = config.get("network", {})
        trace_cfg = config.get("traceroute", {})
        log_cfg = config.get("logging", {})

        # Schedule
        self.interval: int = int(sched_cfg.get("interval", 60))
        self.offline_threshold: int = int(sched_cfg.get("offline_threshold", 5))

        # Subnet — use from config if present, else sensible default
        self.subnet: str = net_cfg.get("subnet", "10.15.117.0/24")
        self.interface: str = net_cfg.get("interface", "eno2")

        # Scan depth control — runtime-mutable
        self.scan_depth: int = int(net_cfg.get("scan_depth", 1))
        self.additional_subnets: List[str] = list(net_cfg.get("additional_subnets", []))
        self.max_subnet_count: int = int(net_cfg.get("max_subnet_count", 5))

        # Subnet chunking threshold
        self.large_subnet_chunk_size: int = int(net_cfg.get("large_subnet_chunk_size", 256))

        # Scanner instances
        self._ping = PingScanner(
            timeout=float(net_cfg.get("ping_timeout", 1.0)),
            concurrency=int(net_cfg.get("ping_concurrency", 200)),
        )
        self._traceroute = TracerouteScanner(
            max_hops=int(trace_cfg.get("max_hops", 30)),
            timeout=float(trace_cfg.get("timeout", 3.0)),
            targets=list(trace_cfg.get("targets", ["8.8.8.8"])),
        )
        self._arp = ARPReader()

        # Logging
        self._log_path: str = log_cfg.get("file", "logs/events.jsonl")
        self._log_dir: str = os.path.dirname(self._log_path)

        # State
        self._device_states: Dict[str, int] = {}   # IP → consecutive failures
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._manual_scan_in_progress: bool = False  # guard for manual scan_now
        self._scan_abort = threading.Event()

        # Local network info (set by main.py after detection)
        self.local_ip: Optional[str] = None
        self.gateway: Optional[str] = None
        self.local_mac: str = ""
        self._trace_cfg: dict = trace_cfg

        # WebSocket manager (injected later)
        self._ws_manager: Any = None
        # Thread-safe WS broadcast callback (set by main.py)
        self._ws_broadcast: Optional[Callable[[str], None]] = None
        # GraphBuilder (injected by main.py; persistent across cycles for diff)
        self._graph_builder: Any = None
        # Latest topology data (thread-safe via _lock)
        self._last_devices: List[dict] = []
        self._last_links: List[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_ws_manager(self, manager: Any, broadcast_fn: Optional[Callable[[str], None]] = None) -> None:
        """Inject a WebSocket manager for real-time event broadcasting.

        *broadcast_fn* is a thread-safe synchronous callback that sends
        a JSON string to all connected WebSocket clients.
        """
        self._ws_manager = manager
        self._ws_broadcast = broadcast_fn

    def set_graph_builder(self, builder: Any) -> None:
        """Inject a persistent GraphBuilder for diffs across cycles."""
        self._graph_builder = builder

    def get_last_devices(self) -> List[dict]:
        """Return the most recent classified devices (thread-safe)."""
        with self._lock:
            return list(self._last_devices)

    def get_last_links(self) -> List[dict]:
        """Return the most recent inferred links (thread-safe)."""
        with self._lock:
            return list(self._last_links)

    def start(self) -> None:
        """Start the background scan thread."""
        if self._running:
            logger.warning("ScanScheduler: already running")
            return
        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True, name="scan-scheduler")
        self._thread.start()
        logger.info("ScanScheduler: started (interval=%ds)", self.interval)

    def stop(self) -> None:
        """Stop the background scan thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval + 10)
        logger.info("ScanScheduler: stopped")

    def get_state(self) -> dict:
        """Return a snapshot of current scanner state."""
        with self._lock:
            return {
                "running": self._running,
                "interval": self.interval,
                "subnet": self.subnet,
                "scan_depth": self.scan_depth,
                "additional_subnets": list(self.additional_subnets),
                "device_states": dict(self._device_states),
                "offline_threshold": self.offline_threshold,
            }

    def update_config(self, config_update: dict) -> None:
        """Apply runtime config changes (called via WebSocket)."""
        if "scan_depth" in config_update:
            try:
                new_depth = int(config_update["scan_depth"])
            except (ValueError, TypeError):
                logger.warning("Invalid scan_depth value: %s", config_update["scan_depth"])
                new_depth = None
            if new_depth in (1, 2, 3):
                if new_depth != self.scan_depth:
                    self.scan_depth = new_depth
                    self._scan_abort.set()
                    logger.info("scan_depth changed to %d, aborting current scan", new_depth)
            else:
                logger.warning("Invalid scan_depth: %s", new_depth)
        if "additional_subnets" in config_update:
            import ipaddress
            new_subs = list(config_update["additional_subnets"])
            validated: List[str] = []
            for sn in new_subs:
                try:
                    ipaddress.IPv4Network(str(sn).strip(), strict=False)
                    validated.append(str(sn).strip())
                except (ValueError, AttributeError):
                    logger.warning(
                        "Invalid CIDR in additional_subnets: %s, skipping", sn
                    )
            if validated != self.additional_subnets:
                self.additional_subnets = validated
                self._scan_abort.set()
                logger.info("additional_subnets updated, aborting current scan: %s", self.additional_subnets)
            else:
                logger.info("additional_subnets unchanged: %s", self.additional_subnets)
        if "max_subnet_count" in config_update:
            new_count = int(config_update["max_subnet_count"])
            if new_count != self.max_subnet_count:
                self.max_subnet_count = new_count
                self._scan_abort.set()
                logger.info("max_subnet_count updated to %d, aborting current scan", new_count)

    # ------------------------------------------------------------------
    # Subnet chunking helper
    # ------------------------------------------------------------------

    def _chunk_large_subnet(self, subnet: str) -> Optional[List[str]]:
        """Split *subnet* into /24 blocks when it exceeds the chunk threshold.

        Returns the original subnet unchanged when small enough;
        otherwise returns a flat list of /24 blocks that cover the
        original address space.

        Returns ``None`` when *subnet* is invalid or too large
        (prefix length < 8), and the caller should skip it.
        """
        import ipaddress

        # P1-4: catch invalid CIDR strings
        try:
            net = ipaddress.IPv4Network(subnet, strict=False)
        except ValueError:
            logger.warning(
                "Invalid CIDR in subnet list: %s, skipping", subnet
            )
            return None

        # P0-1: reject subnets with prefix length < 8 (e.g. /0, /1, … /7)
        if net.prefixlen < 8:
            logger.warning(
                "Subnet %s is too large (prefix /%d < /8), skipping",
                subnet, net.prefixlen,
            )
            return None

        # num_addresses includes network & broadcast addresses
        host_count = net.num_addresses - 2
        if host_count <= self.large_subnet_chunk_size:
            return [subnet]

        try:
            chunks = list(net.subnets(new_prefix=24))
        except ValueError:
            # prefix is already >= 24 — can't subdivide further
            return [subnet]

        # P1-①: cap chunks to prevent OOM on very large subnets
        if len(chunks) > self.MAX_CHUNKS:
            logger.warning(
                "Subnet %s produces %d chunks, capping to %d (MAX_CHUNKS=%d)",
                subnet, len(chunks), self.MAX_CHUNKS, self.MAX_CHUNKS,
            )
            chunks = chunks[:self.MAX_CHUNKS]

        logger.info(
            "_chunk_large_subnet: %s (%d hosts) → %d /24 blocks",
            subnet, host_count, len(chunks),
        )
        return [str(c) for c in chunks]

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _scan_loop(self) -> None:
        """Main loop: sleep between cycles."""
        while self._running:
            try:
                self._scan_cycle()
            except Exception:
                logger.exception("Scan cycle failed — continuing")
            # Sleep in small chunks so stop() is responsive
            deadline = time.monotonic() + self.interval
            while self._running and time.monotonic() < deadline:
                time.sleep(0.5)

    # ------------------------------------------------------------------
    # Depth-3 auto-discovery targets
    # ------------------------------------------------------------------
    DEPTH3_TARGETS: list = [
        "8.8.8.8",          # Google DNS
        "1.1.1.1",          # Cloudflare DNS
        "114.114.114.114",  # 114 DNS
        "223.5.5.5",        # AliDNS
        "180.76.76.76",     # Baidu
        "119.29.29.29",     # Tencent
        "101.6.6.6",        # Tsinghua
        "202.112.0.35",     # CERNET
    ]

    # Maximum number of /24 chunks per subnet (prevents OOM on /8–/14)
    MAX_CHUNKS: int = 1024

    # ------------------------------------------------------------------
    # Single scan cycle
    # ------------------------------------------------------------------

    def _scan_cycle(self) -> None:
        """Execute one complete scan: ping → ARP → traceroute.

        Behaviour depends on ``self.scan_depth``:

        * depth=1 — local subnet only, 3 external traceroute targets
        * depth=2 — local subnet + additional_subnets, 3 targets
        * depth=3 — local subnet + auto-discovered + additional_subnets,
          8 traceroute targets → collect routers → infer /24 subnets

        All subnets (local + remote chunks) are pinged in a single
        ``scan_subnets_parallel`` call for maximum throughput.
        Large subnets (> *large_subnet_chunk_size* hosts) are
        automatically split into /24 blocks.
        """
        # P1-②: guard against concurrent scan cycles with lock
        if not self._scan_lock.acquire(blocking=False):
            logger.debug("Scan cycle already in progress, skipping")
            self._scan_abort.set()
            return
        self._scan_abort.clear()
        try:
            cycle_start = time.monotonic()
            ts = datetime.now(timezone.utc).isoformat()

            depth = self.scan_depth
            logger.info("Scan cycle starting (depth=%d)", depth)

            self._emit_event({"type": "scan_start", "timestamp": ts,
            "subnet": self.subnet, "scan_depth": depth})

            # ── Determine traceroute targets per depth ────────────────
            if depth == 3:
                trace_targets = list(self.DEPTH3_TARGETS)
            else:
                trace_targets = list(self._trace_cfg.get("targets", ["8.8.8.8"]))

            # ── Compute remote subnets to scan ────────────────────────
            remote_subnets: List[str] = []

            if depth >= 2 and self.additional_subnets:
                remote_subnets.extend(self.additional_subnets)

            # ── Phase 0 (depth=3 only): Traceroute + auto-discovery ──
            # Run traceroute *first* so auto-discovered subnets can be
            # included in the parallel ping phase below.
            trace_results: Dict[str, List[dict]] = {}
            if depth == 3:
                self._emit_event({"type": "progress", "phase": "traceroute",
                "message": f"Starting traceroute ({len(trace_targets)} targets)"})
                try:
                    tr = TracerouteScanner(
                    max_hops=int(self._trace_cfg.get("max_hops", 30)),
                    timeout=float(self._trace_cfg.get("timeout", 3.0)),
                    targets=trace_targets,
                    )
                    trace_results = tr.trace_all()
                except Exception as exc:
                    logger.error("Traceroute phase failed: %s", exc)
                    trace_results = {}

                gateway_hops = sum(1 for hops in trace_results.values()
                                   for h in hops if h["ip"])
                self._emit_event({
                    "type": "progress",
                    "phase": "traceroute",
                    "message": f"Traceroute complete: {len(trace_results)} targets, {gateway_hops} hops",
                    "targets": len(trace_results),
                    "total_hops": gateway_hops,
                })

                # Auto-discover subnets from traceroute
                discovered = self._discover_subnets_from_traceroute(trace_results)
                logger.info("Auto-discovered subnets: %s", discovered)
                for sn in discovered:
                    if sn not in remote_subnets and sn != self.subnet:
                        remote_subnets.append(sn)
                # Cap remote subnets
                if len(remote_subnets) > self.max_subnet_count:
                    remote_subnets = remote_subnets[:self.max_subnet_count]
                    logger.info("Remote subnets capped to %d: %s",
                                self.max_subnet_count, remote_subnets)

                # ── Checkpoint: abort after Phase 0 traceroute ───────
                if self._scan_abort.is_set():
                    self._scan_abort.clear()
                    self._emit_event({
                        "type": "scan_cancelled",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "reason": "config_changed",
                    })
                    logger.info("Scan cycle aborted by config change (after traceroute)")
                    return

            # ── Phase 1: Ping ALL subnets in parallel ─────────────────
            import ipaddress as _ip  # local import for IPv4Address sort key, overlap check

            # P1-1: chunk local subnet too (e.g. if configured as /16)
            local_chunks = self._chunk_large_subnet(self.subnet)
            if local_chunks is None:
                # local subnet is invalid — fall back to original string
                local_chunks = [self.subnet]

            # P1-2: check each remote subnet/chunk for overlap with local subnet
            local_net = _ip.IPv4Network(self.subnet, strict=False)
            remote_chunks_map: Dict[str, List[str]] = {}  # original → chunk list
            remote_ping_subnets: List[str] = []
            for sn in remote_subnets:
                chunks = self._chunk_large_subnet(sn)
                # P1-4: skip invalid/rejected subnets (returned None)
                if chunks is None:
                    continue

                # P1-2: check if any chunk overlaps with local subnet
                overlaps_local = False
                for chunk in chunks:
                    try:
                        chunk_net = _ip.IPv4Network(chunk, strict=False)
                        if chunk_net.overlaps(local_net):
                            logger.debug(
                                "Remote chunk %s overlaps local subnet %s, skipping remote subnet %s",
                                chunk, self.subnet, sn,
                            )
                            overlaps_local = True
                            break
                    except ValueError:
                        pass

                if overlaps_local:
                    continue

                remote_chunks_map[sn] = chunks
                remote_ping_subnets.extend(chunks)

            # P2-③: deduplicate remote chunks against each other (remove subsets)
            if len(remote_ping_subnets) > 1:
                seen_networks: list = []
                deduped: List[str] = []
                for chunk_str in sorted(remote_ping_subnets,
                key=lambda s: _ip.IPv4Network(s, strict=False).prefixlen):
                    chunk_net = _ip.IPv4Network(chunk_str, strict=False)
                    if any(chunk_net.subnet_of(existing) for existing in seen_networks):
                        logger.debug("Skipping duplicate remote chunk %s (subset of existing)", chunk_str)
                        continue
                    seen_networks.append(chunk_net)
                    deduped.append(chunk_str)
                if len(deduped) < len(remote_ping_subnets):
                    logger.info("Deduped remote chunks: %d → %d", len(remote_ping_subnets), len(deduped))
                    # Also filter remote_chunks_map to match
                    deduped_set = set(deduped)
                    remote_chunks_map = {
                    sn: [c for c in chunks if c in deduped_set]
                    for sn, chunks in remote_chunks_map.items()
                    }
                    remote_ping_subnets = deduped

            # Combine local + remote for single parallel scan
            all_ping_subnets = local_chunks + remote_ping_subnets

            total_count = len(all_ping_subnets)
            self._emit_event({"type": "progress", "phase": "ping",
            "message": f"Starting parallel ping ({total_count} subnets, depth={depth})"})

            # Single parallel scan: all IPs across all subnets share one pool
            ping_results = self._ping.scan_subnets_parallel(all_ping_subnets)

            # Extract local alive IPs from chunked local subnet(s)
            alive: List[str] = []
            for chunk in local_chunks:
                alive.extend(ping_results.get(chunk, []))
                alive.sort(key=lambda a: _ip.IPv4Address(a))
                alive_set = set(alive)
                logger.info("Ping phase: %d alive on local subnet %s", len(alive), self.subnet)

            # Build remote_alive dict: map each original remote subnet to
            # the union of its chunk results (or directly for un-chunked subnets).
            remote_alive: Dict[str, List[str]] = {}
            for sn, chunks in remote_chunks_map.items():
                combined: List[str] = []
                for chunk in chunks:
                    combined.extend(ping_results.get(chunk, []))
                    combined.sort(key=lambda a: _ip.IPv4Address(a))
                    remote_alive[sn] = combined

            # Emit per-subnet progress events *after* parallel scan completes
            idx = 0
            for sn in all_ping_subnets:
                idx += 1
                ips = ping_results.get(sn, [])
                self._emit_event({
                "type": "progress",
                "phase": "ping",
                "message": f"Subnet {sn}: {len(ips)} alive ({idx}/{total_count} subnets scanned)",
                "alive_count": len(ips),
                })

            # Update device states (local subnet only)
            self._update_device_states(alive_set)

            # ── Checkpoint A: abort after ping phase ─────────────────
            if self._scan_abort.is_set():
                self._scan_abort.clear()
                self._emit_event({
                    "type": "scan_cancelled",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": "config_changed",
                })
                logger.info("Scan cycle aborted by config change (after ping)")
                return

            # ── Phase 2: ARP (local only — no ARP for remote subnets) ─
            self._emit_event({"type": "progress", "phase": "arp",
            "message": "Reading ARP cache"})
            try:
                arp_entries = self._arp.read_cache(interface=self.interface)
            except Exception as exc:
                logger.error("ARP phase failed: %s", exc)
                arp_entries = []

            self._emit_event({
                "type": "progress",
                "phase": "arp",
                "message": f"ARP complete: {len(arp_entries)} entries",
                "arp_count": len(arp_entries),
            })

            # ── Checkpoint B: abort after ARP phase ──────────────────
            if self._scan_abort.is_set():
                self._scan_abort.clear()
                self._emit_event({
                    "type": "scan_cancelled",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": "config_changed",
                })
                logger.info("Scan cycle aborted by config change (after ARP)")
                return

            # ── Phase 3: Traceroute (not already done for depth=3) ───
            if depth != 3:
                self._emit_event({"type": "progress", "phase": "traceroute",
                "message": f"Starting traceroute ({len(trace_targets)} targets)"})
                try:
                    tr = TracerouteScanner(
                    max_hops=int(self._trace_cfg.get("max_hops", 30)),
                    timeout=float(self._trace_cfg.get("timeout", 3.0)),
                    targets=trace_targets,
                    )
                    trace_results = tr.trace_all()
                except Exception as exc:
                    logger.error("Traceroute phase failed: %s", exc)
                    trace_results = {}

                gateway_hops = sum(1 for hops in trace_results.values()
                                   for h in hops if h["ip"])
                self._emit_event({
                    "type": "progress",
                    "phase": "traceroute",
                    "message": f"Traceroute complete: {len(trace_results)} targets, {gateway_hops} hops",
                    "targets": len(trace_results),
                    "total_hops": gateway_hops,
                })

            # ── Checkpoint C: abort after traceroute phase ───────────
            if self._scan_abort.is_set():
                self._scan_abort.clear()
                self._emit_event({
                    "type": "scan_cancelled",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": "config_changed",
                })
                logger.info("Scan cycle aborted by config change (after traceroute)")
                return

            # ── Phase 4: Build topology & broadcast ──────────────────
            try:
                from topology.classifier import DeviceClassifier
                from topology.linker import LinkInferrer
                from topology.graph_builder import GraphBuilder

                local_ip = self.local_ip or "127.0.0.1"
                gateway = self.gateway or "10.15.117.1"

                # Classify local subnet devices (with ARP)
                classifier = DeviceClassifier()
                devices = classifier.classify(
                online_ips=alive, gateway=gateway,
                arp_entries=arp_entries, local_ip=local_ip,
                traceroute_results=trace_results,
                traceroute_targets=trace_targets,
                subnet=self.subnet,
                )

                # Fill local MAC/vendor (not in ARP cache for the local machine)
                if self.local_mac and self.local_mac != "Unknown":
                    for d in devices:
                        if d["type"] == "self" and d["ip"] == local_ip:
                            d["mac"] = self.local_mac
                            d["vendor"] = self._arp.resolve_vendor(self.local_mac)
                            break

                # Classify remote subnet devices (no ARP, is_remote=True)
                local_ips = {d["ip"] for d in devices if d["ip"] is not None}
                for sn, ips in remote_alive.items():
                    remote_devs = classifier.classify_remote(
                        online_ips=ips, subnet=sn, gateway=gateway,
                        traceroute_results=trace_results,
                        traceroute_targets=trace_targets,
                    )
                    # Deduplicate: skip IPs already classified locally
                    remote_devs = [
                        d for d in remote_devs
                        if d["ip"] is None or d["ip"] not in local_ips
                    ]
                    devices.extend(remote_devs)
                    logger.info("Remote subnet %s: %d devices classified (%d added after dedup)",
                                sn, len(remote_devs) + len([d for d in remote_devs if d["ip"] in local_ips]),
                                len(remote_devs))

                linker = LinkInferrer()
                links = linker.infer(
                    devices, self.subnet, gateway,
                    traceroute_results=trace_results,
                )

                # Use persistent builder if injected, else create fresh
                builder = self._graph_builder if self._graph_builder is not None else GraphBuilder()
                builder.build(devices, links)

                topology_data = builder.export_json()

                # Store latest results for API access
                with self._lock:
                    self._last_devices = devices
                    self._last_links = links

                self._emit_event({
                    "type": "topology_update",
                    "data": topology_data,
                })

                # Also check for diffs and emit topology_changed
                diff = builder.diff()
                if any(diff.values()):
                    self._emit_event({
                        "type": "topology_changed",
                        "changes": diff,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
            except Exception as exc:
                logger.error("Topology build failed: %s", exc)

            elapsed = round(time.monotonic() - cycle_start, 2)
            summary = {
            "type": "scan_complete",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": elapsed,
            "alive_hosts": len(alive),
            "arp_entries": len(arp_entries),
            "trace_targets": len(trace_results),
            "remote_subnets": list(remote_alive.keys()) if remote_alive else [],
            "scan_depth": depth,
            }
            self._emit_event(summary)
            logger.info("Scan cycle complete in %.1fs (depth=%d)", elapsed, depth)
        finally:
            self._scan_lock.release()

    # ------------------------------------------------------------------
    # Depth-3 auto-discovery helper
    # ------------------------------------------------------------------

    def _discover_subnets_from_traceroute(
        self, trace_results: Dict[str, List[dict]]
    ) -> List[str]:
        """Analyse traceroute hops to infer neighbouring /24 subnets.

        Collects every intermediate router IP, excludes the gateway and
        the final target IPs, then maps each remaining IP to its /24
        prefix (e.g. 192.168.51.1 → 192.168.51.0/24).

        Returns a sorted, de-duplicated list of subnet strings.
        """
        import ipaddress

        router_ips: set = set()
        gateway = self.gateway or ""

        for target, hops in trace_results.items():
            for hop in hops:
                ip = hop.get("ip")
                if ip is None:
                    continue
                # Exclude gateway and the final target
                if ip == gateway:
                    continue
                if ip == target:
                    continue
                router_ips.add(ip)

        subnets: set = set()
        for ip_str in router_ips:
            try:
                addr = ipaddress.IPv4Address(ip_str)
                # Derive the /24 network prefix
                network = ipaddress.IPv4Network(f"{addr}/24", strict=False)
                prefix = str(network)
                # Skip local subnet
                if prefix == self.subnet:
                    continue
                subnets.add(prefix)
            except ValueError:
                logger.debug("Skipping non-IPv4 hop: %s", ip_str)

        return sorted(subnets)

    # ------------------------------------------------------------------
    # Device state tracking
    # ------------------------------------------------------------------

    def _update_device_states(self, alive_set: set) -> None:
        """Compare current alive set against known device states.

        Emit ``device_online`` / ``device_offline`` events on state change,
        and ``device_discovered`` when a previously-unknown IP first appears.
        """
        ts = datetime.now(timezone.utc).isoformat()

        with self._lock:
            # Increment counters for previously-known IPs that are now missing
            for ip in list(self._device_states):
                if ip not in alive_set:
                    self._device_states[ip] += 1
                    if self._device_states[ip] >= self.offline_threshold:
                        self._emit_event({
                            "type": "device_offline",
                            "ip": ip,
                            "timestamp": ts,
                            "consecutive_failures": self._device_states[ip],
                        })
                        # Keep at threshold — don't overflow counters forever
                        self._device_states[ip] = self.offline_threshold
                else:
                    # IP is alive
                    if self._device_states[ip] >= self.offline_threshold:
                        # Previously marked offline → now online
                        self._emit_event({
                            "type": "device_online",
                            "ip": ip,
                            "timestamp": ts,
                        })
                    self._device_states[ip] = 0

            # Add newly-seen IPs
            for ip in alive_set:
                if ip not in self._device_states:
                    self._device_states[ip] = 0
                    self._emit_event({
                        "type": "device_discovered",
                        "ip": ip,
                        "timestamp": ts,
                    })

            # Periodic cleanup: remove entries that have been offline
            # for more than 10× the threshold (avoid unbounded growth).
            stale_limit = self.offline_threshold * 10
            stale_ips = [
                ip for ip, count in self._device_states.items()
                if count >= stale_limit
            ]
            for ip in stale_ips:
                del self._device_states[ip]
                logger.debug("Cleaned up stale device: %s", ip)

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    def _emit_event(self, event: Dict[str, Any]) -> None:
        """Write event to JSON-Lines log and optionally broadcast via WS."""
        # Log file
        try:
            os.makedirs(self._log_dir, exist_ok=True)
            with open(self._log_path, "a") as fh:
                json.dump(event, fh)
                fh.write("\n")
        except OSError as exc:
            logger.error("Failed to write event log: %s", exc)

        # WebSocket broadcast (if sync callback injected)
        if self._ws_broadcast is not None:
            try:
                self._ws_broadcast(json.dumps(event))
            except Exception:
                logger.debug("WS broadcast failed", exc_info=True)


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # Minimal config matching project's config.yaml structure
    DEFAULT_CONFIG = {
        "network": {
            "interface": "eno2",
            "ping_timeout": 1.0,
            "ping_concurrency": 30,
            "subnet": "10.15.117.0/24",
        },
        "traceroute": {
            "max_hops": 30,
            "timeout": 3.0,
            "targets": ["8.8.8.8", "114.114.114.114"],
        },
        "scheduler": {
            "interval": 60,
            "offline_threshold": 5,
        },
        "logging": {
            "file": "logs/events.jsonl",
        },
    }

    parser = argparse.ArgumentParser(description="ScanScheduler self-test")
    parser.add_argument(
        "--cycles", type=int, default=1,
        help="Number of scan cycles (default: 1)",
    )
    args = parser.parse_args()

    scheduler = ScanScheduler(DEFAULT_CONFIG)

    print("=== ScanScheduler: running scan cycle(s) ===")
    for i in range(args.cycles):
        print(f"\n--- Cycle {i + 1} ---")
        scheduler._scan_cycle()
        time.sleep(1)  # brief pause between manual cycles

    print("\n=== Device States ===")
    state = scheduler.get_state()
    print(f"Running: {state['running']}")
    print(f"Subnet: {state['subnet']}")
    print(f"Offline threshold: {state['offline_threshold']}")
    print(f"Tracked devices: {len(state['device_states'])}")
    for ip, count in sorted(state["device_states"].items(), key=lambda x: int(x[0].split(".")[-1])):
        status = "offline" if count >= state["offline_threshold"] else "online"
        print(f"  {ip:<16} → {status} (miss_count={count})")

    print("\n=== Events (last 10 lines) ===")
    try:
        with open("logs/events.jsonl") as fh:
            lines = fh.readlines()
        for line in lines[-10:]:
            obj = json.loads(line)
            print(f"  [{obj.get('type','?')}] {json.dumps(obj, ensure_ascii=False)[:120]}")
    except Exception as exc:
        print(f"  Cannot read log: {exc}")
