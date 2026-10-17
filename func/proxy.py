"""Proxy and Tor support.

* :class:`ProxyPool` handles a single proxy, a rotating list from a file, or a
  Tor SOCKS endpoint.
* :class:`TorController` talks to Tor's control port so the circuit (and
  therefore the exit IP) can be rotated with ``SIGNAL NEWNYM`` while scanning.
"""

from __future__ import annotations

import random
import socket
import threading
import time

from utils.ansi import c

__all__ = ["ProxyPool", "TorController", "tor_proxies", "check_tcp"]


def check_tcp(host: str, port: int, timeout: float = 3.0) -> bool:
    """True when a TCP connection to ``host:port`` succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tor_proxies(host: str = "127.0.0.1", port: int = 9050) -> dict[str, str]:
    """``requests`` proxy mapping for a Tor SOCKS5 endpoint.

    ``socks5h`` makes Tor resolve DNS itself (no DNS leaks).
    """
    url = f"socks5h://{host}:{port}"
    return {"http": url, "https": url}


class ProxyPool:
    """Round-robin/random source of proxies for the HTTP layer."""

    def __init__(
        self,
        single: str | None = None,
        file_path: str | None = None,
        log=None,
        randomize: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._index = 0
        self.proxies: list[str] = []
        self.log = log
        self.randomize = randomize

        if single:
            self.proxies.append(self._normalize(single))
        if file_path:
            self.proxies.extend(self._load_file(file_path))
        self.proxies = [p for p in self.proxies if p]

    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize(raw: str) -> str:
        value = raw.strip()
        if not value:
            return ""
        if "://" not in value:
            value = "http://" + value
        return value

    def _load_file(self, path: str) -> list[str]:
        out: list[str] = []
        try:
            with open(path, "rb") as handle:
                for raw in handle:
                    try:
                        line = raw.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        line = raw.decode("latin-1", errors="replace").strip()
                    if not line or line.startswith("#"):
                        continue
                    out.append(self._normalize(line))
        except OSError as exc:
            if self.log:
                self.log(c(f"could not read proxy file {path}: {exc}", "yellow"))
        return out

    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return bool(self.proxies)

    @property
    def count(self) -> int:
        return len(self.proxies)

    def next(self) -> dict[str, str] | None:
        if not self.proxies:
            return None
        with self._lock:
            if self.randomize or len(self.proxies) > 1:
                if self.randomize:
                    proxy = random.choice(self.proxies)
                else:
                    proxy = self.proxies[self._index % len(self.proxies)]
                    self._index += 1
            else:
                proxy = self.proxies[0]
        return {"http": proxy, "https": proxy}


class TorController:
    """Minimal Tor control-port client (no ``stem`` dependency)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9051,
        password: str | None = None,
        log=None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.password = password
        self.log = log
        self.cookie_file: str | None = None
        self.authenticated = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _recv_until(self, sock: socket.socket, marker: bytes = b"\r\n") -> bytes:
        sock.settimeout(5.0)
        buffer = b""
        while marker not in buffer and len(buffer) < 8192:
            try:
                chunk = sock.recv(1024)
            except (socket.timeout, OSError):
                break
            if not chunk:
                break
            buffer += chunk
        return buffer

    def _read_reply(self, sock: socket.socket) -> str:
        lines: list[str] = []
        sock.settimeout(5.0)
        while True:
            try:
                chunk = sock.recv(4096)
            except (socket.timeout, OSError):
                break
            if not chunk:
                break
            lines.append(chunk.decode("utf-8", "replace"))
            joined = "".join(lines)
            for line in joined.splitlines():
                if len(line) >= 4 and line[3] == " ":
                    return joined
        return "".join(lines)

    def _authenticate(self, sock: socket.socket) -> bool:
        sock.sendall(b"PROTOCOLINFO 1\r\n")
        info = self._read_reply(sock)
        if self.password:
            sock.sendall(f'AUTHENTICATE "{self.password}"\r\n'.encode())
            reply = self._read_reply(sock)
            self.authenticated = "250" in reply
            return self.authenticated

        # no password configured: try null auth, then the cookie file
        sock.sendall(b"AUTHENTICATE\r\n")
        reply = self._read_reply(sock)
        if "250" in reply:
            self.authenticated = True
            return True

        import re

        match = re.search(r'COOKIEFILE="([^"]+)"', info)
        candidates = []
        if match:
            candidates.append(match.group(1).strip('"'))
        candidates += [
            "/var/run/tor/control.authcookie",
            "/run/tor/control.authcookie",
            "/var/lib/tor/control_auth_cookie",
        ]
        for candidate in candidates:
            try:
                with open(candidate, "rb") as handle:
                    cookie = handle.read().strip()
            except OSError:
                continue
            self.cookie_file = candidate
            sock.sendall(b"AUTHENTICATE " + cookie.hex().encode() + b"\r\n")
            reply = self._read_reply(sock)
            if "250" in reply:
                self.authenticated = True
                return True
        if self.log:
            self.log(c("tor control: authentication failed (set --tor-password)", "yellow"))
        return False

    def newnym(self, retries: int = 2) -> bool:
        """Ask Tor for a fresh circuit. Returns True on success."""
        with self._lock:
            for attempt in range(retries + 1):
                try:
                    with socket.create_connection((self.host, self.port), timeout=5.0) as sock:
                        if not self.authenticated and not self._authenticate(sock):
                            return False
                        sock.sendall(b"SIGNAL NEWNYM\r\n")
                        reply = self._read_reply(sock)
                        if "250" in reply:
                            time.sleep(0.6)  # give tor a moment to build the circuit
                            return True
                except OSError:
                    pass
                time.sleep(0.5)
            if self.log:
                self.log(c("tor control: NEWNYM failed (is ControlPort enabled?)", "yellow"))
            return False
