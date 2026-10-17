"""Small, thread-safe TTL/LRU cache used to avoid redoing expensive lookups.

During a spray the same host shows up over and over again: caching the
availability probe, the WordPress version and the detected WAF turns thousands
of redundant requests into a handful.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

__all__ = ["TTLCache"]


class TTLCache:
    def __init__(self, maxsize: int = 50_000, ttl: float = 900.0) -> None:
        self.maxsize = max(64, int(maxsize))
        self.ttl = float(ttl)
        self._data: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Any, default: Any = None) -> Any:
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return default
            expires, value = item
            if expires < now:
                del self._data[key]
                self.misses += 1
                return default
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: Any, value: Any, ttl: float | None = None) -> None:
        with self._lock:
            self._data[key] = (time.time() + (ttl if ttl is not None else self.ttl), value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def get_or_set(self, key: Any, factory, ttl: float | None = None) -> Any:
        sentinel = object()
        value = self.get(key, sentinel)
        if value is not sentinel:
            return value
        value = factory()
        self.set(key, value, ttl)
        return value

    def pop(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            item = self._data.pop(key, None)
            return item[1] if item else default

    def purge(self) -> None:
        now = time.time()
        with self._lock:
            for key in [k for k, (exp, _) in self._data.items() if exp < now]:
                del self._data[key]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._data), "hits": self.hits, "misses": self.misses}

    def __contains__(self, key: Any) -> bool:
        marker = object()
        return self.get(key, marker) is not marker
