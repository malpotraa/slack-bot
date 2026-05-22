"""Bounded in-process caches for the single always-on Cloud Run instance.

Module-level caches accumulate for the life of the process — it only restarts on
deploy — so an unbounded dict is a slow memory leak. These helpers cap that:

  • TTLCache   — LRU + time-expiry value cache.
  • KeyedLocks — per-key asyncio locks with a bounded backing dict (singleflight).

Both are for a single asyncio loop and are not thread-safe.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """LRU + TTL cache.

    `get` returns None for a miss OR an expired entry — so don't store None as a
    meaningful value; use a sentinel (e.g. an empty string) instead.
    """

    def __init__(self, *, maxsize: int = 256, ttl_seconds: float = 300.0) -> None:
        self._maxsize = max(1, maxsize)
        self._ttl = ttl_seconds
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        item = self._data.get(key)
        if item is None:
            return None
        ts, value = item
        if time.monotonic() - ts >= self._ttl:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        self._evict()

    def pop(self, key: K) -> None:
        self._data.pop(key, None)

    def _evict(self) -> None:
        now = time.monotonic()
        for k in [k for k, (ts, _) in self._data.items() if now - ts >= self._ttl]:
            self._data.pop(k, None)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)


class KeyedLocks:
    """Per-key asyncio locks with a bounded backing dict — for singleflight.

    Callers MUST acquire with no `await` between `get(key)` and entering the
    `async with` (i.e. `async with locks.get(key):`). Pruning only ever drops
    UNHELD locks; with no yield point in that gap, a lock can't be pruned out
    from under a caller that's about to acquire it.
    """

    def __init__(self, *, maxsize: int = 512) -> None:
        self._maxsize = max(1, maxsize)
        self._locks: dict[object, asyncio.Lock] = {}

    def get(self, key: object) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            if len(self._locks) >= self._maxsize:
                self._prune()
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _prune(self) -> None:
        for k in [k for k, lk in list(self._locks.items()) if not lk.locked()]:
            self._locks.pop(k, None)

    def __len__(self) -> int:
        return len(self._locks)
