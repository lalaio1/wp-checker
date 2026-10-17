"""Shared scan context: settings, HTTP layer, caches and statistics."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from func.net import HttpPool
from utils.cache import TTLCache
from utils.resources import DynamicCapacity

__all__ = ["ScanContext", "ScanStats", "STATUS_KEYS"]

STATUS_KEYS = {
    "Valid": "valid",
    "Invalid": "invalid",
    "Offline": "offline",
    "Blocked": "blocked",
    "Error": "error",
    "NotWordPress": "notwp",
}


class ScanStats:
    """Thread safe counters for the final summary."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters = {
            "valid": 0,
            "invalid": 0,
            "offline": 0,
            "blocked": 0,
            "error": 0,
            "notwp": 0,
            "processed": 0,
        }
        self.requests = 0

    def add(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + amount
            self.counters["processed"] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.counters)

    @property
    def processed(self) -> int:
        with self._lock:
            return self.counters["processed"]


class AtomicCounter:
    """Tiny thread-safe counter (used for Tor circuit rotation)."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, amount: int = 1) -> int:
        with self._lock:
            self._value += amount
            return self._value

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


@dataclass
class ScanContext:
    args: Any
    http: HttpPool
    log: Any = None
    verbose_log: Any = None
    capacity: DynamicCapacity | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    ping_cache: TTLCache = field(default_factory=lambda: TTLCache(maxsize=200_000, ttl=900))
    version_cache: TTLCache = field(default_factory=lambda: TTLCache(maxsize=200_000, ttl=3600))
    permission_cache: TTLCache = field(default_factory=lambda: TTLCache(maxsize=200_000, ttl=3600))
    tor_controller: Any = None
    tor_rotate_every: int = 0
    proxy_count: int = 0
    counter: AtomicCounter = field(default_factory=AtomicCounter)
    stats: ScanStats = field(default_factory=ScanStats)

    # ------------------------------------------------------------------ #
    def maybe_rotate_tor(self, index: int) -> None:
        if not self.tor_controller or not self.tor_rotate_every:
            return
        if index and index % self.tor_rotate_every == 0:
            if self.log:
                self.log(f"tor: requesting a new circuit (after {index} targets)")
            self.tor_controller.newnym()
