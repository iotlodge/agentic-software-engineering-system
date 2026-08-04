"""In-process TTL/LRU cache for hot code lookups.

The database remains the source of truth; the cache only ever holds values
read from it, bounded by TTL. Updates and disables invalidate immediately.
Expiry is *not* cached away: the cached entry stores ``expires_at`` and the
caller re-evaluates it per request, so a cached entry can never outlive its
link's expiry.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, maxsize: int = 1024, ttl: float = 30.0,
                 clock=time.monotonic):
        self.maxsize = maxsize
        self.ttl = ttl
        self.clock = clock
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            stored_at, value = item
            if self.clock() - stored_at >= self.ttl:
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (self.clock(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._data), "hits": self.hits,
                    "misses": self.misses, "ttl_s": self.ttl}
