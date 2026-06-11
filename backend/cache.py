"""Small in-process cache for read-heavy dashboard APIs.

The production deployment runs a single backend process, so an in-memory TTL
cache gives most of the Redis benefit without adding another service.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from typing import Any


_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, Any]] = {}
_STATS = {"hits": 0, "misses": 0, "sets": 0, "invalidations": 0}


def cached(key: str, ttl_seconds: int, factory: Callable[[], Any]) -> Any:
    """Return cached value or compute it with ``factory``.

    Values are deep-copied on get/set so route callers cannot accidentally
    mutate the shared cached object.
    """
    now = time.time()
    with _LOCK:
        item = _CACHE.get(key)
        if item and item[0] > now:
            _STATS["hits"] += 1
            return copy.deepcopy(item[1])
        if item:
            _CACHE.pop(key, None)
        _STATS["misses"] += 1
    value = factory()
    with _LOCK:
        _CACHE[key] = (now + max(1, int(ttl_seconds or 1)), copy.deepcopy(value))
        _STATS["sets"] += 1
    return value


def invalidate(prefix: str = "") -> int:
    """Invalidate all cache keys, or only keys matching a prefix."""
    with _LOCK:
        if not prefix:
            count = len(_CACHE)
            _CACHE.clear()
        else:
            keys = [key for key in _CACHE if key.startswith(prefix)]
            count = len(keys)
            for key in keys:
                _CACHE.pop(key, None)
        _STATS["invalidations"] += count
        return count


def stats() -> dict:
    with _LOCK:
        return {**_STATS, "size": len(_CACHE), "keys": sorted(_CACHE.keys())[:100]}
