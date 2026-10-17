"""Reachability probe (fast TCP connect with IPv6/DNS handling)."""

from __future__ import annotations

import socket
from urllib.parse import urlsplit

__all__ = ["ping_site"]


def ping_site(site_url: str, timeout: float = 6.0) -> bool:
    """Return True when a TCP connection to the site's HTTP(S) port works.

    The default port of the URL scheme is tried first, then its counterpart
    (80 <-> 443) so http-only and https-only hosts are both detected.
    """
    candidate = site_url if "://" in site_url else f"http://{site_url}"
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        if not host:
            return False
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False

    ports = [port]
    if port == 80:
        ports.append(443)
    elif port == 443:
        ports.append(80)

    for port in ports:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            continue
        for family, socktype, proto, _canon, sockaddr in infos[:4]:
            try:
                with socket.socket(family, socktype, proto) as sock:
                    sock.settimeout(timeout)
                    sock.connect(sockaddr)
                    return True
            except OSError:
                continue
    return False
