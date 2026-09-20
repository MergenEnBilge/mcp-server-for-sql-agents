"""Redis-backed cache. Everything is best-effort; see base.py for the rules."""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from mcp_sql_server.cache.base import Cache, NullCache
from mcp_sql_server.config import Settings

logger = logging.getLogger(__name__)

PREFIX = "mcpsql"

# A slow Redis must not make requests slow, so give up quickly and take the slow path.
SOCKET_TIMEOUT_S = 0.5


class RedisCache(Cache):
    def __init__(self, client: Redis) -> None:
        self._redis = client

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

    async def get(self, key: str) -> str | None:
        try:
            value = await self._redis.get(f"{PREFIX}:{key}")
        except (RedisError, OSError, TimeoutError) as exc:
            _warn("get", exc)
            return None
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        try:
            await self._redis.set(f"{PREFIX}:{key}", value, ex=ttl_s)
        except (RedisError, OSError, TimeoutError) as exc:
            _warn("set", exc)

    async def version(self, family: str) -> int | None:
        try:
            value = await self._redis.get(f"{PREFIX}:version:{family}")
        except (RedisError, OSError, TimeoutError) as exc:
            _warn("version", exc)
            return None
        return int(value) if value is not None else 0

    async def bump(self, family: str) -> None:
        try:
            await self._redis.incr(f"{PREFIX}:version:{family}")
        except (RedisError, OSError, TimeoutError) as exc:
            # Unlike a failed read, a failed bump matters: entries that should have been
            # invalidated may be served until their TTL runs out. Say so loudly.
            logger.error(
                "could not invalidate cache family %r; stale entries may be served: %s", family, exc
            )

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except (RedisError, OSError, TimeoutError):
            return False

    async def close(self) -> None:
        await self._redis.aclose()


def create_cache(settings: Settings) -> Cache:
    """Redis if configured, otherwise a cache that does nothing."""
    if settings.redis_url is None:
        return NullCache()
    return RedisCache.from_url(settings.redis_url.get_secret_value())


def _warn(operation: str, exc: Exception) -> None:
    logger.warning("cache %s failed, continuing without the cache: %s", operation, exc)
