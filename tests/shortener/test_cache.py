"""Cache correctness: TTL expiry, LRU bounds, invalidation."""

from shortener.cache import TTLCache


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_hit_within_ttl_miss_after():
    clock = FakeClock()
    cache = TTLCache(ttl=10, clock=clock)
    cache.put("a", 1)
    assert cache.get("a") == 1
    clock.t = 9.9
    assert cache.get("a") == 1
    clock.t = 10.0
    assert cache.get("a") is None  # entry cannot outlive its TTL
    assert cache.stats()["hits"] == 2 and cache.stats()["misses"] == 1


def test_lru_eviction_at_maxsize():
    cache = TTLCache(maxsize=2, ttl=100)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")          # a is now most-recently used
    cache.put("c", 3)       # evicts b
    assert cache.get("b") is None
    assert cache.get("a") == 1 and cache.get("c") == 3


def test_invalidate_removes_immediately():
    cache = TTLCache(ttl=100)
    cache.put("a", 1)
    cache.invalidate("a")
    assert cache.get("a") is None
