"""
TracerouteScanner — UDP-based traceroute using Linux ERRQUEUE.

Sends UDP probes with ascending TTL and reads ICMP Time Exceeded /
Port Unreachable messages from the socket error queue.
"""

from __future__ import annotations

import logging
import select
import socket
import struct
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Linux constants (not exposed by the stdlib socket module)
IP_RECVERR: int = 11         # IP_RECVERR
MSG_ERRQUEUE: int = 0x2000   # MSG_ERRQUEUE
SO_EE_ORIGIN_ICMP: int = 2   # origin_type for ICMP errors

# Ancillary data layout for struct sock_extended_err (16 bytes)
# =iBBBBiI = int(ee_errno) + B(origin) + B(type) + B(code) + B(pad)
#            + int(info) + unsigned(pad2)
_EE_FMT: str = "=iBBBBiI"
_EE_LEN: int = struct.calcsize(_EE_FMT)


class TracerouteScanner:
    """UDP-based traceroute with configurable max-hops and timeout."""

    def __init__(
        self,
        max_hops: int = 30,
        timeout: float = 3.0,
        targets: Optional[List[str]] = None,
    ) -> None:
        self.max_hops = max_hops
        self.timeout = timeout
        self.targets = targets or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trace(self, target: str) -> List[dict]:
        """Trace route to *target*, returning per-hop information.

        Each item: ``{"ttl": int, "ip": str|None, "latency_ms": float|None}``.
        """
        logger.info("Traceroute: %s (max_hops=%d, timeout=%.1fs)", target, self.max_hops, self.timeout)
        hops: List[dict] = []
        try:
            resolved = socket.getaddrinfo(target, None, socket.AF_INET, socket.SOCK_DGRAM)
            target_ip = resolved[0][4][0]
        except socket.gaierror:
            logger.error("Traceroute: cannot resolve %s", target)
            return hops

        for ttl in range(1, self.max_hops + 1):
            hop = {"ttl": ttl, "ip": None, "latency_ms": None}
            t_start = time.monotonic()
            reply_ip = self._probe(target_ip, ttl)
            if reply_ip is not None:
                hop["ip"] = reply_ip
                hop["latency_ms"] = round((time.monotonic() - t_start) * 1000, 2)
            hops.append(hop)

            # Destination reached
            if reply_ip == target_ip:
                break

        return hops

    def trace_all(self) -> Dict[str, List[dict]]:
        """Run ``trace()`` for every configured target, returning a dict."""
        results: Dict[str, List[dict]] = {}
        for t in self.targets:
            results[t] = self.trace(t)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _probe(self, target_ip: str, ttl: int) -> Optional[str]:
        """Send one UDP probe and return the router IP from the error queue.

        Router IP is parsed from the ``sockaddr_in`` that follows the
        ``sock_extended_err`` structure in the ancillary data — **not**
        from ``recvmsg``'s ``msg_name`` (which is the original destination).
        """
        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, IP_RECVERR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            sock.setblocking(False)

            # Send probe to a high, unlikely-to-be-open UDP port
            dst_port = 33434 + ttl
            sock.sendto(b"PROBE", (target_ip, dst_port))

            ready, _, _ = select.select([sock], [], [], self.timeout)
            if not ready:
                return None

            # Read from the error queue
            data, ancdata, _flags, addr = sock.recvmsg(4096, 1024, MSG_ERRQUEUE)

            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if cmsg_level == socket.IPPROTO_IP and cmsg_type == IP_RECVERR:
                    # Ensure we have enough bytes to unpack sock_extended_err
                    if len(cmsg_data) >= _EE_LEN:
                        ee = struct.unpack(_EE_FMT, cmsg_data[:_EE_LEN])
                        # ee[0]=ee_errno, ee[1]=origin, ee[2]=type, ee[3]=code
                        if ee[1] == SO_EE_ORIGIN_ICMP:
                            # --- Parse router IP from ancillary data ---
                            # After sock_extended_err comes the sockaddr_in
                            # of the router that sent the ICMP error.
                            # sin_addr (4 bytes at offset 4) is stored in
                            # network byte order, ready for inet_ntoa.
                            router_ip: Optional[str] = None
                            extra = cmsg_data[_EE_LEN:]
                            if len(extra) >= 8:
                                sin_family = struct.unpack('=H', extra[0:2])[0]
                                if sin_family == 2:  # AF_INET
                                    # extra[4:8] is sin_addr in network byte order
                                    router_ip = socket.inet_ntoa(extra[4:8])

                            # Fallback to msg_name if ancillary parse failed
                            if router_ip is None and addr:
                                router_ip = addr[0]

                            # --- Parse ICMP type from returned packet ---
                            # data contains the inner IP header + ICMP header.
                            # IP header length = (first byte & 0x0F) * 4.
                            if len(data) >= 28:
                                ip_ver_ihl = data[0]
                                ip_hdr_len = (ip_ver_ihl & 0x0F) * 4
                                icmp_offset = ip_hdr_len
                                if len(data) > icmp_offset:
                                    icmp_type = data[icmp_offset]
                                    # type 11 = Time Exceeded (router)
                                    # type 3  = Destination Unreachable
                                    #   code 3 = Port Unreachable (reached target)
                                    logger.debug(
                                        "Probe ttl=%d → router=%s icmp_type=%d",
                                        ttl, router_ip, icmp_type,
                                    )

                            return router_ip
            return None
        except (OSError, TimeoutError) as exc:
            logger.debug("Probe error ttl=%d → %s: %s", ttl, target_ip, exc)
            return None
        finally:
            if sock is not None:
                sock.close()


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="TracerouteScanner self-test")
    parser.add_argument(
        "targets", nargs="*", default=["10.15.117.1", "8.8.8.8"],
        help="Target hosts to trace (default: 10.15.117.1 8.8.8.8)",
    )
    parser.add_argument("--max-hops", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()

    scanner = TracerouteScanner(max_hops=args.max_hops, timeout=args.timeout, targets=args.targets)

    for tgt in args.targets:
        print(f"\n=== traceroute to {tgt} ===")
        for hop in scanner.trace(tgt):
            ip_str = hop["ip"] or "*"
            lat = f"  {hop['latency_ms']} ms" if hop["latency_ms"] is not None else ""
            print(f"  {hop['ttl']:>2}  {ip_str:<16}{lat}")
