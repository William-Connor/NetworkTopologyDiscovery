"""
NetworkDetect — Auto-detect active interface, IP, subnet, and gateway.

Uses ``/proc/net/route`` for gateway detection and ``ip addr`` for
IP/subnet info.  No root privileges required.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import struct
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def detect_network(interface: str = "auto") -> dict:
    """Auto-detect network configuration.

    If *interface* is ``"auto"``, automatically chooses the active
    (default-route) interface and its IP/subnet.

    Returns a dict::

        {
            "interface": "eno2",
            "ip": "10.15.117.85",
            "subnet": "10.15.117.0/24",
            "gateway": "10.15.117.1",
        }
    """
    # 1. Detect gateway and the default-route interface
    gw, gw_iface = get_gateway()
    logger.info("Detected gateway: %s on %s", gw, gw_iface)

    # 2. If interface=auto, use the default-route interface
    if interface == "auto":
        if gw_iface is None:
            # Fallback: try to find any non-loopback interface
            gw_iface = _find_first_active_iface()
        interface = gw_iface or "eno2"

    # 3. Detect IP and subnet for the chosen interface
    ip_addr, subnet = get_ip_subnet(interface)

    return {
        "interface": interface,
        "ip": ip_addr,
        "subnet": subnet,
        "gateway": gw,
    }


def get_gateway() -> Tuple[Optional[str], Optional[str]]:
    """Parse ``/proc/net/route`` to find the default gateway.

    Returns ``(gateway_ip, interface)`` or ``(None, None)``.
    """
    try:
        with open("/proc/net/route") as f:
            for line in f:
                fields = line.strip().split()
                if len(fields) < 11:
                    continue
                # Column 1: Destination (00000000 = default)
                # Column 3: Flags (RTF_GATEWAY = 0x2)
                if fields[1] == "00000000" and (int(fields[3], 16) & 2):
                    gw_int = int(fields[2], 16)
                    gw = socket.inet_ntoa(struct.pack("<I", gw_int))
                    iface = fields[0]
                    return gw, iface
    except (OSError, ValueError, struct.error) as exc:
        logger.warning("Failed to read /proc/net/route: %s", exc)

    return None, None


def get_ip_subnet(interface: str) -> Tuple[str, str]:
    """Parse ``ip addr show <interface>`` to extract the primary IPv4
    address and its CIDR subnet.

    Returns ``(ip, subnet)``, e.g. ``("10.15.117.85", "10.15.117.0/24")``.
    """
    ip_addr = "127.0.0.1"
    subnet = "127.0.0.0/24"

    try:
        output = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", interface],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("ip addr failed for %s: %s", interface, exc)
        return ip_addr, subnet

    for line in output.splitlines():
        parts = line.strip().split()
        # Typical format:
        # 2: eno2  inet 10.15.117.85/24  brd 10.15.117.255  scope global  ...
        for i, part in enumerate(parts):
            if part == "inet" and i + 1 < len(parts):
                cidr = parts[i + 1]
                if "/" in cidr:
                    ip_str, prefix_str = cidr.split("/")
                    prefix = int(prefix_str)
                    ip_addr = ip_str
                    # Compute network address
                    net = ipaddress.IPv4Network(f"{ip_str}/{prefix}", strict=False)
                    subnet = str(net)
                break

    return ip_addr, subnet


def get_local_ip(interface: str) -> str:
    """Return the local IP for *interface* (convenience wrapper)."""
    ip_addr, _ = get_ip_subnet(interface)
    return ip_addr


def get_local_mac(interface: str) -> str:
    """Read MAC address from /sys/class/net/<interface>/address."""
    try:
        with open(f"/sys/class/net/{interface}/address") as f:
            return f.read().strip().upper()
    except (FileNotFoundError, PermissionError):
        return "Unknown"


def _find_first_active_iface() -> Optional[str]:
    """Fallback: return the first non-loopback interface with an IPv4 address."""
    try:
        output = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    for line in output.splitlines():
        if " lo " in line:
            continue
        parts = line.strip().split()
        # 2: eno2  inet 10.15.117.85/24  ...
        if len(parts) >= 2 and parts[0].endswith(":"):
            iface = parts[1]
            return iface

    return None


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="NetworkDetect self-test")
    parser.add_argument(
        "--interface", "-i", default="auto",
        help="Interface name or 'auto' (default: auto)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Network Auto-Detection — Self-test")
    print("=" * 60)

    net = detect_network(interface=args.interface)
    print(f"  Interface : {net['interface']}")
    print(f"  IP        : {net['ip']}")
    print(f"  Subnet    : {net['subnet']}")
    print(f"  Gateway   : {net['gateway']}")
