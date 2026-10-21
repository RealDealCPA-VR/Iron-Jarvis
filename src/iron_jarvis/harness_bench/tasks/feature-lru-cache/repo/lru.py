"""A least-recently-used cache.

Implement LRUCache::

    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")        # 1 -- and "a" is now the most recently used
    cache.put("c", 3)     # drops "b", the least recently used
    cache.get("b")        # None
    len(cache)            # 2

- ``LRUCache(capacity)``: ``capacity`` must be at least 1, otherwise ValueError.
- ``get(key, default=None)``: the value, or ``default`` when the key is not
  held. A hit marks the key as the most recently used.
- ``put(key, value)``: insert or update. An update also marks the key as the
  most recently used. When the cache then holds more than ``capacity`` keys,
  the least recently used key is dropped.
- ``len(cache)``: how many keys it holds.
- ``key in cache``: True when the key is held. This does NOT mark it as used.
"""


class LRUCache:
    def __init__(self, capacity):
        raise NotImplementedError("LRUCache is not written yet")
