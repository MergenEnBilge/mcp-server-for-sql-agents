"""Redis-backed cache. Everything is best-effort; see base.py for the rules.

If Redis stops answering, the first failing call notices and every call after it skips Redis
entirely for a short cooldown (a circuit breaker). Without that, each request would sit through
several timeouts in a row, which turns "the cache is down" into "the whole service is slow".
"""

import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from mcp_sql_server.cache.base import Cache, NullCache
from mcp_sql_server.config import Settings

logger = logging.getLogger(__name__)

PREFIX = "mcpsql"

# A slow Redis must not make requests slow, so give up quickly and take the slow path.
SOCKET_TIMEOUT_S = 0.5

# After a failure, don't touch Redis at all for this long.
COOLDOWN_S = 10.0

_FAILURES = (RedisError, OSError, TimeoutError)


class RedisCache(Cache):
    def __init__(self, client: Redis) -> None:
        self._redis = client
        self._down_until = 0.0

    @classmethod
    def from_url(cls, url: str) -> "RedisCache":
        return cls(
            Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=SOCKET_TIMEOUT_S,
                socket_connect_timeout=SOCKET_TIMEOUT_S,
            )
        )

    def _available(self) -> bool:
        return time.monotonic() >= self._down_until

    def _failed(self, operation: str, exc: Exception) -> None:
        if self._available():  # log when the breaker trips, not on every skipped call
            logger.warning(
                "cache %s failed; skipping Redis for %ds and continuing without the cache: %s",
                operation,
                COOLDOWN_S,
                exc,
            )
        self._down_until = time.monotonic() + COOLDOWN_S

    async def get(self, key: str) -> str | None:
        if not self._available():
            return None
        try:
            value = await self._redis.get(f"{PREFIX}:{key}")
        except _FAILURES as exc:
            self._failed("get", exc)
            return None
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        if not self._available():
            return
        try:
            await self._redis.set(f"{PREFIX}:{key}", value, ex=ttl_s)
        except _FAILURES as exc:
            self._failed("set", exc)

    async def version(self, family: str) -> int | None:
        if not self._available():
            return None
        try:
            value = await self._redis.get(f"{PREFIX}:version:{family}")
        except _FAILURES as exc:
            self._failed("version", exc)
            return None
        return int(value) if value is not None else 0

    async def bump(self, family: str) -> None:
        # Always attempted, even during a cooldown: this is the write that keeps permission
        # changes immediate, and it happens rarely (only when an admin changes something).
        try:
            await self._redis.incr(f"{PREFIX}:version:{family}")
        except _FAILURES as exc:
            # Unlike a failed read, a failed bump matters: entries that should have been
            # invalidated may be served until their TTL runs out. Say so loudly.
            self._down_until = time.monotonic() + COOLDOWN_S
            logger.error(
                "could not invalidate cache family %r; stale entries may be served: %s", family, exc
            )

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except _FAILURES:
            return False

    async def close(self) -> None:
        await self._redis.aclose()


def create_cache(settings: Settings) -> Cache:
    """Redis if configured, otherwise a cache that does nothing."""
    if settings.redis_url is None:
        return NullCache()
    return RedisCache.from_url(settings.redis_url.get_secret_value())
