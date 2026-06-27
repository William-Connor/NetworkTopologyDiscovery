"""
PingScanner — Concurrent ICMP Echo (ping) subnet scanning.

Uses system ``ping`` via subprocess; no raw sockets required.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import subprocess
from typing import Dict, List

logger = logging.getLogger(__name__)


class PingScanner:
    """Concurrent ping-based host discovery for a /24 subnet."""

    def __init__(self, timeout: float = 1.0, concurrency: int = 200) -> None:
        self.timeout = timeout
        self.concurrency = concurrency

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_subnet(self, subnet: str) -> List[str]:
        """Ping every usable host in *subnet* concurrently.

        Returns a sorted list of IP strings that responded.
        """
        network = ipaddress.IPv4Network(subnet, strict=False)
        ips = [str(h) for h in network.hosts()]  # skips network & broadcast
        logger.info(
            "PingScanner: scanning %s (%d hosts, timeout=%.1fs, concurrency=%d)",
            subnet, len(ips), self.timeout, self.concurrency,
        )

        alive: List[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            future_map = {ex.submit(self._ping_one, ip): ip for ip in ips}
            for future in concurrent.futures.as_completed(future_map):
                ip = future_map[future]
                try:
                    if future.result():
                        alive.append(ip)
                except Exception:
                    logger.debug("Ping error for %s", ip, exc_info=True)

        alive.sort(key=lambda a: ipaddress.IPv4Address(a))
        logger.info("PingScanner: %d/%d hosts alive", len(alive), len(ips))
        return alive

    def scan_subnets(self, subnets: List[str]) -> Dict[str, List[str]]:
        """Scan multiple subnets sequentially.

        Returns a dict mapping each subnet string to its list of alive IPs,
        e.g. ``{"10.15.117.0/24": ["10.15.117.49",...], ...}``.

        Prefer ``scan_subnets_parallel()`` for better throughput when
        scanning multiple subnets, as it uses a single shared thread pool.
        """
        results: Dict[str, List[str]] = {}
        for sn in subnets:
            logger.info("PingScanner: scanning subnet %s", sn)
            results[sn] = self.scan_subnet(sn)
        return results

    def scan_subnets_parallel(
        self,
        subnets: List[str],
        concurrency: int | None = None,
    ) -> Dict[str, List[str]]:
        """Scan multiple subnets in parallel using a single shared thread pool.

        All IPs across all *subnets* are submitted to one
        ``ThreadPoolExecutor`` for maximum throughput.  Results are
        collected via :func:`as_completed` so that failures in one
        subnet do not affect the others.

        Args:
            subnets: List of subnet strings (e.g. ``["10.15.117.0/24",
                     "10.11.0.0/24", ...]``).
            concurrency: Max concurrent pings.  Defaults to
                         ``self.concurrency`` when omitted.

        Returns:
            Dict mapping each *subnet* string to its sorted list of alive IPs.
        """
        if concurrency is None:
            concurrency = self.concurrency

        # Pre-compute every (subnet, ip) task
        all_tasks: list[tuple[str, str]] = []
        for sn in subnets:
            network = ipaddress.IPv4Network(sn, strict=False)
            for host in network.hosts():
                all_tasks.append((sn, str(host)))

        results: Dict[str, List[str]] = {sn: [] for sn in subnets}

        if not all_tasks:
            logger.warning("scan_subnets_parallel: no hosts to scan")
            return results

        logger.info(
            "scan_subnets_parallel: %d subnets, %d hosts total (concurrency=%d)",
            len(subnets), len(all_tasks), concurrency,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
            future_map = {
                ex.submit(self._ping_one, ip): (sn, ip)
                for sn, ip in all_tasks
            }
            for future in concurrent.futures.as_completed(future_map):
                sn, ip = future_map[future]
                try:
                    if future.result():
                        results[sn].append(ip)
                except Exception:
                    logger.debug("Ping error for %s/%s", sn, ip, exc_info=True)

        # Sort each subnet's results numerically
        for sn in results:
            results[sn].sort(key=lambda a: ipaddress.IPv4Address(a))
            logger.info(
                "scan_subnets_parallel: %s → %d alive",
                sn, len(results[sn]),
            )

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ping_one(self, ip: str) -> bool:
        """Return ``True`` if *ip* replies to a single ping."""
        rc = subprocess.run(
            ["ping", "-c", "1", "-W", str(self.timeout), ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=self.timeout + 2,
        )
        return rc.returncode == 0


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="PingScanner self-test")
    parser.add_argument(
        "subnet", nargs="?", default="10.15.117.0/24",
        help="Subnet to scan (default: 10.15.117.0/24)",
    )
    parser.add_argument(
        "--parallel", nargs="*", default=None,
        help="Additional subnets for parallel scan test",
    )
    args = parser.parse_args()

    scanner = PingScanner(timeout=1.0, concurrency=200)

    if args.parallel is not None:
        subnets = [args.subnet] + list(args.parallel)
        results = scanner.scan_subnets_parallel(subnets)
        for sn, hosts in results.items():
            print(f"{sn} — Online hosts ({len(hosts)}):")
            for h in hosts:
                print(f"  {h}")
    else:
        hosts = scanner.scan_subnet(args.subnet)
        print(f"Online hosts ({len(hosts)}):")
        for h in hosts:
            print(f"  {h}")
