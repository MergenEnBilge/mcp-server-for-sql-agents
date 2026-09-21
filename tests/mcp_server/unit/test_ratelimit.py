"""Fair-use limits: calls per minute (shared through the cache) and calls at once."""

import asyncio
import time

import pytest
from fake_cache import BrokenCache, MemoryCache

from mcp_sql_server.ratelimit import RateLimited, RateLimiter


async def use(limiter: RateLimiter, key: str = "alice") -> None:
    async with limiter.slot(key):
        pass


async def test_calls_up_to_the_limit_are_allowed_then_refused():
    limiter = RateLimiter(MemoryCache(), per_minute=3)
    for _ in range(3):
        await use(limiter)
    with pytest.raises(RateLimited, match=r"limit is 3 a minute.*Wait about \d+ seconds"):
        await use(limiter)


async def test_each_caller_has_their_own_allowance():
    limiter = RateLimiter(MemoryCache(), per_minute=1)
    await use(limiter, "alice")
    await use(limiter, "bob")
    with pytest.raises(RateLimited):
        await use(limiter, "alice")


async def test_the_count_is_shared_through_the_cache_so_replicas_add_up():
    cache = MemoryCache()
    first, second = RateLimiter(cache, per_minute=2), RateLimiter(cache, per_minute=2)
    await use(first)
    await use(second)
    with pytest.raises(RateLimited):
        await use(first)


async def test_without_a_working_cache_each_process_counts_for_itself():
    limiter = RateLimiter(BrokenCache(), per_minute=2)
    await use(limiter)
    await use(limiter)
    with pytest.raises(RateLimited):
        await use(limiter)


async def test_the_allowance_comes_back_in_the_next_minute(monkeypatch):
    limiter = RateLimiter(BrokenCache(), per_minute=1)
    await use(limiter)
    with pytest.raises(RateLimited):
        await use(limiter)
    now = time.time()
    monkeypatch.setattr("mcp_sql_server.ratelimit.time.time", lambda: now + 61)
    await use(limiter)


async def test_zero_means_no_limit():
    limiter = RateLimiter(MemoryCache(), per_minute=0, max_concurrent=0)
    for _ in range(500):
        await use(limiter)


async def test_only_so_many_calls_may_run_at_the_same_time():
    limiter = RateLimiter(max_concurrent=2)
    release = asyncio.Event()

    async def slow() -> None:
        async with limiter.slot("alice"):
            await release.wait()

    running = [asyncio.create_task(slow()) for _ in range(2)]
    await asyncio.sleep(0)
    with pytest.raises(RateLimited, match="already have 2 requests running"):
        await use(limiter)
    await use(limiter, "bob")  # someone else is unaffected
    release.set()
    await asyncio.gather(*running)
    await use(limiter)  # and the place is free again


async def test_a_failing_call_gives_its_place_back():
    limiter = RateLimiter(max_concurrent=1)
    with pytest.raises(RuntimeError):
        async with limiter.slot("alice"):
            raise RuntimeError("boom")
    await use(limiter)
