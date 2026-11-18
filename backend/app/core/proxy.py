"""Client address and scheme behind a reverse proxy, trusting forwarded headers only from configured proxies.

Uvicorn's own proxy-header handling stays off (see ``app/serve.py``); this module is the only place that reads
``X-Forwarded-For`` / ``X-Forwarded-Proto``, and only when the direct peer is listed in
``TRACEFORGE_TRUSTED_PROXIES``. With the default (empty) list nothing changes: the direct peer is the client.

``X-Forwarded-For`` is read right to left. Each proxy appends the address it received the request from, so the
right-most entries were written by our own proxies and the left-most ones may have been typed by the client.
Walking from the right and skipping trusted proxies, the first address that is not a trusted proxy is the client.
A malformed entry stops the walk at the last trusted hop, so a client cannot inject a value past it.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from starlette.requests import Request

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(value: str) -> list[Network]:
    """'127.0.0.1, 10.0.0.0/8' -> networks. Raises ValueError on anything that is not an IP or CIDR."""
    networks: list[Network] = []
    for item in value.split(","):
        item = item.strip()
        if item:
            networks.append(ipaddress.ip_network(item, strict=False))
    return networks


def _ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    value = value.strip()
    if value.startswith("[") and "]" in value:  # "[::1]:8080" -> "::1"
        value = value[1 : value.index("]")]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    # "::ffff:10.0.0.1" is the IPv4 client seen through an IPv6 socket.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _trusted(address: ipaddress.IPv4Address | ipaddress.IPv6Address | None, networks: Sequence[Network]) -> bool:
    return address is not None and any(address in network for network in networks)


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def client_ip(request: Request, networks: Sequence[Network]) -> str:
    """The client's address: the direct peer, unless that peer is a trusted proxy that forwarded the request."""
    peer = _peer(request)
    if not networks or not _trusted(_ip(peer), networks):
        return peer
    entries = [part for header in request.headers.getlist("x-forwarded-for") for part in header.split(",")]
    client = peer
    for entry in reversed(entries):
        address = _ip(entry)
        if address is None:
            break  # malformed: keep the last trusted hop rather than anything to its left
        client = str(address)
        if not _trusted(address, networks):
            break
    return client


def request_scheme(request: Request, networks: Sequence[Network]) -> str:
    """``https`` when a trusted proxy says the client connected over TLS; otherwise the connection's own scheme."""
    if networks and _trusted(_ip(_peer(request)), networks):
        values = [v.strip().lower() for h in request.headers.getlist("x-forwarded-proto") for v in h.split(",")]
        # The nearest proxy appends last; accept only the two meaningful values.
        if values and values[-1] in ("http", "https"):
            return values[-1]
    return request.url.scheme
