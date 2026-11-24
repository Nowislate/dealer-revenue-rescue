"""Local-network check for setup Step 0.1.

The question Step 0.1 asks is narrow: *is this machine on the store's
local network?* The check answers it from the machine's own addresses:
it looks for a non-loopback IPv4 address in a private range
(10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, RFC 1918).

What the check does, exactly (locked decision 5: local network only).
It runs only when someone presses "Run network check"; the island-mode
button makes no call at all. It asks the operating system for this
host's own IPv4 addresses by looking up the host's own name
(``socket.getaddrinfo(socket.gethostname())``). It opens no socket and
no connection. Windows, the only supported platform (decision 4),
answers a lookup of the computer's own name from the addresses already
assigned to its network adapters. On other systems the same call
follows the system's name settings (hosts file, and DNS if those
settings say so), which is one reason the app is Windows only.

Standard library only.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Optional, Sequence


_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


@dataclass
class NetworkCheckResult:
    """Structured result of the LAN check."""

    ok: bool
    local_ip: Optional[str]
    message: str
    addresses_seen: tuple[str, ...] = ()


def evaluate_addresses(addresses: Sequence[str]) -> NetworkCheckResult:
    """The check itself, as a pure function of a list of IP strings.

    Split out from the OS call so tests (and the wizard, when a checker
    is injected) never touch the network stack.
    """
    ipv4s: list[str] = []
    for raw in addresses:
        try:
            addr = ipaddress.ip_address(str(raw).strip())
        except ValueError:
            continue
        if isinstance(addr, ipaddress.IPv4Address) and not addr.is_loopback:
            ipv4s.append(str(addr))
    for candidate in ipv4s:
        addr = ipaddress.ip_address(candidate)
        # Explicit RFC 1918 ranges only: ipaddress' is_private also
        # matches documentation/link-local ranges, which are NOT a LAN.
        if any(addr in net for net in _RFC1918_NETWORKS):
            return NetworkCheckResult(
                ok=True,
                local_ip=candidate,
                message=(
                    f"This machine is on a local network "
                    f"(address {candidate})."
                ),
                addresses_seen=tuple(ipv4s),
            )
    if ipv4s:
        return NetworkCheckResult(
            ok=False,
            local_ip=ipv4s[0],
            message=(
                "This machine has a network address, but it is not a "
                "private local-network address. Connect the machine to "
                "the store's local network and try again."
            ),
            addresses_seen=tuple(ipv4s),
        )
    return NetworkCheckResult(
        ok=False,
        local_ip=None,
        message=(
            "No local network address found. Connect this machine to "
            "the store's local network (cable or Wi-Fi) and try again."
        ),
        addresses_seen=(),
    )


def local_ipv4_addresses() -> list[str]:
    """This host's own IPv4 addresses, from the OS: a lookup of the
    host's own name. No socket or connection is opened (see the module
    docstring for how each platform answers the lookup)."""
    hostname = socket.gethostname()
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


def check_lan(addresses: Optional[Sequence[str]] = None) -> NetworkCheckResult:
    """Run the LAN check.

    ``addresses`` may be injected (tests, or a caller that already
    enumerated addresses); otherwise the host's own addresses are read.
    """
    if addresses is None:
        addresses = local_ipv4_addresses()
    return evaluate_addresses(addresses)
