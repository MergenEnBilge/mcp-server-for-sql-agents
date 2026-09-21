"""Fair use: how many tool calls one person may make, and how many at the same moment.

Two limits, both per caller:

  * a rate: at most N calls in any one-minute window. The count lives in the shared cache
    (Redis), so it holds across every replica. Without Redis each process counts for itself,
    which is looser (N per replica) but never blocks anyone by mistake.
  * concurrency: at most M calls running right now. This one is per process by nature; it
    stops one caller from tying up every database connection a replica owns.

A limit of 0 turns it off. Being limited is an ordinary, explainable error for the model
("wait 12 seconds"), not a failure of the server.
"""

import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp_sql_server.cache.base import Cache, NullCache
from mcp_sql_server.errors import McpSqlError

WINDOW_S = 60


class RateLimited(McpSqlError):
    """The caller made too many requests. The message says how long to wait."""


class RateLimiter:
    def __init__(
        self,
        cache: Cache | None = None,
        *,
        per_minute: int = 0,
        max_concurrent: int = 0,
    ) -> None:
        self._cache = cache or NullCache()
        self._per_minute = per_minute
        self._max_concurrent = max_concurrent
        self._running: defaultdict[str, int] = defaultdict(int)
        self._local_counts: dict[tuple[str, int], int] = {}

    @asynccontextmanager
    async def slot(self, key: str) -> AsyncIterator[None]:
        """Hold one call's place for `key`, or raise `RateLimited`."""
        await self._check_rate(key)
        if self._max_concurrent and self._running[key] >= self._max_concurrent:
            raise RateLimited(
                f"You already have {self._max_concurrent} requests running. "
                "Wait for one to finish before starting another."
            )
        self._running[key] += 1
        try:
            yield
        finally:
            self._running[key] -= 1
            if self._running[key] <= 0:
                del self._running[key]

    async def _check_rate(self, key: str) -> None:
        if not self._per_minute:
            return
        now = time.time()
        window = int(now // WINDOW_S)
        used = await self._cache.count(f"rate:{key}:{window}", WINDOW_S)
        if used is None:
            used = self._count_locally(key, window)
        if used > self._per_minute:
            wait = max(1, int((window + 1) * WINDOW_S - now) + 1)
            raise RateLimited(
                f"Too many requests: the limit is {self._per_minute} a minute. "
                f"Wait about {wait} seconds and try again."
            )

    def _count_locally(self, key: str, window: int) -> int:
        # Forget windows that are over, so this can't grow without bound.
        for stale in [k for k in self._local_counts if k[1] < window]:
            del self._local_counts[stale]
        used = self._local_counts.get((key, window), 0) + 1
        self._local_counts[(key, window)] = used
        return used
