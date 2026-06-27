"""
ARPReader — Read the kernel ARP cache and resolve MAC vendor prefixes.

Uses ``/proc/net/arp`` (no raw sockets) and the ``manuf`` library with the
latest Wireshark OUI/manuf database (auto-cached locally).
"""

from __future__ import annotations

import logging
import time
import urllib.request
from pathlib import Path
from typing import Dict, List

from manuf import manuf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OUI database cache
# ---------------------------------------------------------------------------

_MANUF_URL = "https://www.wireshark.org/download/automated/data/manuf"
# Refresh the cached database at most once per 30 days
_CACHE_MAX_AGE_SEC = 30 * 86400


def _project_root() -> Path:
    """Path to the project root (parent of this scanner/ directory)."""
    return Path(__file__).resolve().parent.parent


def _get_manuf_cache_path() -> Path:
    """Return the path where the Wireshark manuf file is cached."""
    return _project_root() / "data" / "manuf.txt"


def _cache_manuf_db() -> Path:
    """Ensure a reasonably-fresh Wireshark manuf file is cached locally.

    Downloads from the official Wireshark automated data mirror when the
    local copy is missing or older than ``_CACHE_MAX_AGE_SEC``.
    Returns the path to the cached file.
    """
    cache_path = _get_manuf_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    need_download = True
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < _CACHE_MAX_AGE_SEC:
            logger.debug("manuf cache is %d days old, reusing", int(age / 86400))
            need_download = False
        else:
            logger.info("manuf cache expired (%d days old), refreshing", int(age / 86400))

    if need_download:
        logger.info("Downloading latest Wireshark manuf database …")
        try:
            urllib.request.urlretrieve(_MANUF_URL, cache_path)
            logger.info("manuf database cached → %s (%d bytes)", cache_path, cache_path.stat().st_size)
        except Exception as exc:
            logger.warning("Failed to download manuf DB: %s", exc)
            if cache_path.exists():
                logger.info("Falling back to existing cached manuf file")
            else:
                raise

    return cache_path


class ARPReader:
    """Reads ``/proc/net/arp`` and resolves OUI vendors via manuf.

    Uses the latest Wireshark manuf database, cached in ``data/manuf.txt``.
    """

    _ARP_PATH: str = "/proc/net/arp"

    # /proc/net/arp column positions (0-indexed)
    _COL_IP = 0
    _COL_HW_TYPE = 1
    _COL_FLAGS = 2
    _COL_HW_ADDR = 3
    _COL_MASK = 4
    _COL_DEVICE = 5

    def __init__(self) -> None:
        manuf_path = str(_cache_manuf_db())
        self._parser = manuf.MacParser(manuf_name=manuf_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_cache(self, interface: str = "eno2") -> List[dict]:
        """Return ARP entries filtered by *interface*.

        Each entry: ``{"ip": str, "mac": str, "vendor": str, "interface": str}``.
        """
        entries: List[dict] = []
        try:
            with open(self._ARP_PATH) as fh:
                lines = fh.readlines()
        except OSError as exc:
            logger.error("Cannot read %s: %s", self._ARP_PATH, exc)
            return entries

        if len(lines) < 2:
            return entries  # header only

        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 6:
                continue
            if parts[self._COL_DEVICE] != interface:
                continue
            mac = parts[self._COL_HW_ADDR].upper()
            # Filter incomplete ARP entries (zero / empty MAC)
            if mac in ("00:00:00:00:00:00", "0:0:0:0:0:0", ""):
                continue
            entry = {
                "ip": parts[self._COL_IP],
                "mac": mac,
                "vendor": self.resolve_vendor(mac),
                "interface": parts[self._COL_DEVICE],
            }
            entries.append(entry)

        logger.info("ARPReader: %d entries on %s", len(entries), interface)
        return entries

    def resolve_vendor(self, mac: str) -> str:
        """Look up the full OUI vendor name for *mac*.

        Uses ``manuf_long`` (the full vendor string) to avoid truncation;
        falls back to ``manuf`` if the long form is empty.
        """
        try:
            result = self._parser.get_all(mac)
            if result:
                vendor = (result.manuf_long or result.manuf or "").strip()
                if vendor:
                    return vendor
        except Exception:
            logger.debug("manuf lookup failed for %s", mac, exc_info=True)
        return "Unknown"


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="ARPReader self-test")
    parser.add_argument(
        "--interface", "-i", default="eno2",
        help="Interface to filter (default: eno2)",
    )
    args = parser.parse_args()

    reader = ARPReader()
    entries = reader.read_cache(interface=args.interface)

    print(f"ARP cache on {args.interface} ({len(entries)} entries):\n")
    print(f"{'IP':<16} {'MAC':<18} Vendor")
    print("-" * 60)
    for e in entries:
        print(f"{e['ip']:<16} {e['mac']:<18} {e['vendor']}")
