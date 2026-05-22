"""Bounded in-process cache helpers — TTLCache + KeyedLocks."""

import asyncio

from app.utils.cache import KeyedLocks, TTLCache


# ── TTLCache ───────────────────────────────────────────────────────────────


def test_ttlcache_get_set_and_miss():
    c: TTLCache[str, str] = TTLCache(maxsize=8, ttl_seconds=100)
    assert c.get("k") is None
    c.set("k", "v")
    assert c.get("k") == "v"


def test_ttlcache_entries_expire():
    c: TTLCache[str, str] = TTLCache(maxsize=8, ttl_seconds=0)
    c.set("k", "v")
    assert c.get("k") is None


def test_ttlcache_lru_eviction_over_capacity():
    c: TTLCache[str, int] = TTLCache(maxsize=2, ttl_seconds=100)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # over cap → evicts the oldest ("a")
    assert len(c) == 2
    assert c.get("a") is None
    assert c.get("b") == 2 and c.get("c") == 3


def test_ttlcache_get_refreshes_recency():
    c: TTLCache[str, int] = TTLCache(maxsize=2, ttl_seconds=100)
    c.set("a", 1)
    c.set("b", 2)
    c.get("a")  # "a" becomes most-recently-used
    c.set("c", 3)  # evicts "b", not "a"
    assert c.get("a") == 1
    assert c.get("b") is None


def test_ttlcache_pop():
    c: TTLCache[str, str] = TTLCache()
    c.set("k", "v")
    c.pop("k")
    assert c.get("k") is None


# ── KeyedLocks ─────────────────────────────────────────────────────────────


def test_keyedlocks_same_key_same_lock():
    locks = KeyedLocks()
    assert locks.get("x") is locks.get("x")
    assert locks.get("x") is not locks.get("y")


def test_keyedlocks_prunes_unheld_when_full():
    locks = KeyedLocks(maxsize=2)
    locks.get("a")
    locks.get("b")
    locks.get("c")  # over cap → unheld locks pruned
    assert len(locks) <= 2


def test_keyedlocks_held_lock_survives_prune():
    async def run() -> None:
        locks = KeyedLocks(maxsize=2)
        held = locks.get("held")
        async with held:
            locks.get("a")
            locks.get("b")
            locks.get("c")  # forces pruning while "held" is locked
            assert locks.get("held") is held  # the held lock was not pruned

    asyncio.run(run())
