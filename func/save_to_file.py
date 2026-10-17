"""Block oriented writer for ``-v/--valid``, ``-i/--invalid``, ``-off``, ``--blocked``.

The layout matches what wp-checker has always produced (and what it can read
back as input): ``URL`` is the site base and ``URL Login`` the full login page::

    URL: https://example.com
    URL Login: https://example.com/wp-login.php
    Username: admin
    Password: secret
    Status: Valid
    WP Version: 6.4.3
    Role: Administrator
    ...
    ----------------------------------------
"""

from __future__ import annotations

import threading

__all__ = ["BlockWriter"]

_SEPARATOR = "-" * 40

# label used in the file -> record key
_LABELS = (
    ("WP Version", "wp_version"),
    ("Role", "role"),
    ("Admin", "admin"),
    ("Name", "name"),
    ("Capabilities", "capabilities"),
    ("Plugin Install", "plugin_install"),
    ("HTTP Code", "http_code"),
    ("WAF", "waf"),
    ("Error", "error"),
    ("Elapsed", "elapsed"),
)


class BlockWriter:
    def __init__(self, path: str, append: bool = False) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._handle = open(path, "a" if append else "w", encoding="utf-8")
        self.count = 0

    def write(self, record: dict) -> None:
        url = record.get("url", "") or ""
        login_url = f"{url}/wp-login.php" if url else ""
        lines = [
            f"URL: {url}",
            f"URL Login: {login_url}",
            f"Username: {record.get('username', '')}",
            f"Password: {record.get('password', '')}",
            f"Status: {record.get('status', '')}",
        ]
        for label, key in _LABELS:
            value = record.get(key, "")
            if value not in ("", None):
                lines.append(f"{label}: {value}")
        lines.append(_SEPARATOR)
        payload = "\n".join(lines) + "\n"
        with self._lock:
            self._handle.write(payload)
            self._handle.flush()
            self.count += 1

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()
