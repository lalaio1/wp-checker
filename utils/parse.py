"""Parsing of target files and normalisation of WordPress URLs.

Two input layouts are supported, on purpose:

1. **Combo lines** (the classic format)::

       http://example.com:admin:secret
       https://example.com/wp-login.php:admin:secret
       https://example.com:8080/blog:admin:sec:ret   # password may contain ':'

2. **Block format** (what wp-checker itself writes to ``-v``/``-i`` files)::

       URL: https://example.com/wp-admin/options-general.php
       Username: admin
       Password: secret
       Status: Valid
       WP Version: 6.4.3
       ----------------------------------------

Files written on Windows (CRLF) and legacy latin-1 encoded files are handled
transparently, lines are streamed so multi-gigabyte files never blow up RAM.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterator

__all__ = [
    "Target",
    "iter_targets",
    "parse_combo_line",
    "normalize_base_url",
    "count_targets",
    "WP_LOGIN_PATH",
]

WP_LOGIN_PATH = "/wp-login.php"

_BLOCK_KEYS = {
    "url": "url",
    "site": "url",
    "username": "username",
    "user": "username",
    "login": "username",
    "password": "password",
    "pass": "password",
    "pwd": "password",
}
# Keys that belong to block files but are not credentials - ignored on purpose.
_IGNORED_BLOCK_KEYS = {
    "status",
    "wp version",
    "wp_version",
    "role",
    "admin",
    "name",
    "display name",
    "capabilities",
    "caps",
    "plugin install",
    "plugin_install",
    "waf",
    "bypass",
    "error",
    "elapsed",
    "http code",
    "http_code",
}
_KNOWN_BLOCK_KEYS = set(_BLOCK_KEYS) | _IGNORED_BLOCK_KEYS

_BLOCK_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z _-]{0,20})\s*:\s*(?P<value>.*)$")
_SCHEME_RE = re.compile(r"^(?P<scheme>https?)://(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_HOST_RE = re.compile(r"^[A-Za-z0-9._~\-]+(?::[0-9]{1,5})?$")
_IPV6_RE = re.compile(r"^\[[0-9A-Fa-f:.]{2,45}\](?::[0-9]{1,5})?$")
_PORT_RE = re.compile(r"^(?P<port>\d{1,5})(?P<path>/.*)?$")

_WP_ENDPOINTS = (
    "/wp-login.php",
    "/wp-signup.php",
    "/wp-activate.php",
    "/wp-cron.php",
    "/xmlrpc.php",
    "/readme.html",
    "/license.txt",
    "/wp-admin",
    "/wp-content",
    "/wp-includes",
    "/wp-json",
    "/wp-links-opml.php",
    "/wp-trackback.php",
)


@dataclass(slots=True)
class Target:
    """A single credential pair mapped to a WordPress base URL."""

    url: str
    username: str
    password: str
    line: int = 0

    @property
    def login_url(self) -> str:
        return f"{self.url}{WP_LOGIN_PATH}"

    @property
    def fingerprint(self) -> bytes:
        digest = hashlib.blake2b(
            f"{self.url}\x00{self.username}\x00{self.password}".encode("utf-8", "replace"),
            digest_size=8,
        )
        return digest.digest()


# --------------------------------------------------------------------------- #
# URL handling
# --------------------------------------------------------------------------- #
def normalize_base_url(raw: str) -> str | None:
    """Turn any WordPress URL into a clean ``scheme://host[/subdir]`` base URL.

    ``https://x.com/wp-admin/options-general.php`` -> ``https://x.com``
    ``https://x.com/blog/wp-login.php``           -> ``https://x.com/blog``
    ``x.com:8080``                                -> ``https://x.com:8080``

    Scheme-less targets default to ``https://``; the HTTP layer falls back to
    ``http://`` automatically when the host only speaks plain HTTP.
    """
    if not raw:
        return None
    url = raw.strip().strip("'\"<>,;")
    if not url:
        return None
    if not _SCHEME_RE.match(url):
        if url.lower().startswith("//"):
            url = "https:" + url
        else:
            url = "https://" + url

    match = _SCHEME_RE.match(url)
    if not match:
        return None
    scheme = match.group("scheme").lower()
    rest = match.group("rest").split("#", 1)[0].split("?", 1)[0]

    slash = rest.find("/")
    if slash == -1:
        host, path = rest, ""
    else:
        host, path = rest[:slash], rest[slash:]
    host = host.rstrip(".")
    if not host or "@" in host or not (_HOST_RE.match(host) or _IPV6_RE.match(host)):
        return None

    lowered = path.lower()
    cut = len(path)
    for endpoint in _WP_ENDPOINTS:
        idx = lowered.find(endpoint)
        if idx != -1 and idx < cut:
            cut = idx
    path = path[:cut].rstrip("/")
    if path and not path.startswith("/"):
        path = "/" + path

    return f"{scheme}://{host}{path}"


def parse_combo_line(line: str) -> Target | None:
    """Parse ``url:user:password``.

    Handles the three shapes found in the wild:

    * ``http://host/path:user:pass``           (classic)
    * ``http://host:8080/path:user:pass``      (non standard port)
    * ``http://host:user:pa:ss:word``          (password containing ``:``)
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None

    match = _SCHEME_RE.match(text)
    if match:
        scheme = match.group("scheme").lower()
        rest = match.group("rest")
    else:
        scheme = "https"  # modern default; the network layer retries over http
        rest = text

    if not rest:
        return None

    if rest.startswith("["):
        # IPv6 literal without a path: [2001:db8::1]:user:pass / [::1]:8080:user:pass
        # (with a path we simply fall through to the generic branch below)
        end = rest.find("]")
        if end == -1:
            return None
        tail = rest[end + 1 :]
        if tail.startswith(":"):
            host_literal = rest[: end + 1]
            parts = tail[1:].split(":")
            if len(parts) < 2:
                return None
            maybe_port = _PORT_RE.match(parts[0]) if len(parts) >= 3 else None
            if maybe_port and maybe_port.group("port"):
                url_part = f"{host_literal}:{maybe_port.group('port')}"
                username = parts[1]
                password = ":".join(parts[2:])
            else:
                url_part = host_literal
                username = parts[0]
                password = ":".join(parts[1:])
            username = username.strip()
            if not username or not password:
                return None
            base = normalize_base_url(f"{scheme}://{url_part}")
            if not base:
                return None
            return Target(url=base, username=username, password=password)

    slash = rest.find("/")
    if slash > 0:
        host_part, path_part = rest[:slash], rest[slash:]
        # path_part looks like "/wp-login.php:user:password..." (password may
        # itself contain ':' - that is why maxsplit=2 is used)
        pieces = path_part[1:].split(":", 2)
        if len(pieces) < 3:
            return None
        url_part = f"{host_part}/{pieces[0]}"
        username = pieces[1]
        password = pieces[2]
    else:
        tokens = rest.split(":")
        if len(tokens) < 3:
            return None
        maybe_port = _PORT_RE.match(tokens[1]) if len(tokens) >= 4 else None
        if maybe_port and maybe_port.group("port"):
            url_part = f"{tokens[0]}:{maybe_port.group('port')}{maybe_port.group('path') or ''}"
            username = tokens[2]
            password = ":".join(tokens[3:])
        else:
            url_part = tokens[0]
            username = tokens[1]
            password = ":".join(tokens[2:])

    username = username.strip()
    if not username or not password:
        return None
    if " " in url_part or "\t" in url_part:
        return None
    base = normalize_base_url(f"{scheme}://{url_part}")
    if not base:
        return None
    return Target(url=base, username=username, password=password)


# --------------------------------------------------------------------------- #
# file streaming
# --------------------------------------------------------------------------- #
def _iter_lines(path: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, text)`` with utf-8 -> latin-1 fallback decoding."""
    with open(path, "rb") as handle:
        for number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", errors="replace")
            if "\x00" in text:
                text = text.replace("\x00", "")
            yield number, text.rstrip("\r\n")


def iter_targets(
    path: str,
    dedupe: bool = False,
    limit: int | None = None,
    ignore_duplicates_report: bool = False,
) -> Iterator[Target]:
    """Stream :class:`Target` objects out of ``path``.

    ``dedupe`` keeps an 8 byte hash per unique triple which costs ~40 bytes of
    RAM per entry - cheap enough for millions of lines but easy to disable.
    """
    seen: set[bytes] = set()
    emitted = 0
    block: dict[str, str] = {}
    block_line = 0

    def flush() -> Target | None:
        nonlocal block, block_line
        if not block:
            return None
        url = block.get("url")
        username = block.get("username")
        password = block.get("password")
        line_number = block_line
        block, block_line = {}, 0
        if not (url and username and password):
            return None
        base = normalize_base_url(url)
        if not base:
            return None
        return Target(url=base, username=username, password=password, line=line_number)

    for number, text in _iter_lines(path):
        stripped = text.strip()
        if not stripped:
            continue
        # block separator (---- or ====)
        if len(stripped) >= 3 and re.fullmatch(r"[-=*_#]{3,}", stripped):
            target = flush()
            if target:
                yield from _maybe_yield(target, seen, dedupe)
                emitted += 1
                if limit and emitted >= limit:
                    return
            continue

        match = _BLOCK_RE.match(stripped)
        if match:
            key = match.group("key").strip().lower()
            if key in _BLOCK_KEYS or key in _IGNORED_BLOCK_KEYS:
                value = match.group("value").strip()
                if key in _BLOCK_KEYS:
                    if not block_line:
                        block_line = number
                    block[_BLOCK_KEYS[key]] = value
                continue

        # combo line
        target = parse_combo_line(stripped)
        if target:
            target.line = number
            yield from _maybe_yield(target, seen, dedupe)
            emitted += 1
            if limit and emitted >= limit:
                return

    target = flush()
    if target:
        yield from _maybe_yield(target, seen, dedupe)


def _maybe_yield(target: Target, seen: set[bytes], dedupe: bool) -> Iterator[Target]:
    if not dedupe:
        yield target
        return
    fingerprint = target.fingerprint
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    yield target


def count_targets(path: str, dedupe: bool = False, limit: int | None = None) -> int:
    """Estimate how many credentials the file holds (drives the progress bar).

    Without ``dedupe`` this is a fast byte level scan; with ``dedupe`` the file is
    parsed exactly like :func:`iter_targets` so the bar can still reach 100%.
    ``limit`` caps the count the same way ``--limit`` caps the scan.
    """
    if dedupe:
        return sum(1 for _ in iter_targets(path, dedupe=True, limit=limit))

    total = _cheap_count(path)
    if limit:
        total = min(total, limit)
    return total


def _cheap_count(path: str) -> int:
    total = 0
    blocks = 0
    with open(path, "rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", errors="replace")
            stripped = text.strip()
            if not stripped:
                continue
            if re.fullmatch(r"[-=*_#]{3,}", stripped):
                if blocks:
                    total += 1
                    blocks = 0
                continue
            match = _BLOCK_RE.match(stripped)
            if match and match.group("key").strip().lower() in _BLOCK_KEYS:
                blocks = max(blocks, 1)
                continue
            if match and match.group("key").strip().lower() in _IGNORED_BLOCK_KEYS:
                blocks = max(blocks, 1)
                continue
            if _SCHEME_RE.match(stripped) or ":" in stripped:
                total += 1
    return total + (1 if blocks else 0)
