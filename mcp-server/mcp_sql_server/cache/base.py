"""The cache interface, and a cache that does nothing (used when Redis isn't configured).

Two rules for everything built on this:

1. **A cache failure is never a request failure.** If Redis is down or slow, callers just
   take the slow path. (Authentication is the opposite: it fails closed. A cache is an
   optimisation; a login check is not.)
2. **Entries are invalidated by version, not by hunting for keys.** Each cache "family"
   (`meta` for permissions and descriptions, `schema` for reflected table structure) has a
   version number that is part of every key. Bumping it makes every old entry unreachable at
   once. The admin GUI bumps `meta` whenever it changes a permission, so a revoked grant
   takes effect immediately instead of after the TTL runs out.
"""

from abc import ABC, abstractmethod

META = "meta"  # permissions, connection access, table/column descriptions
SCHEMA = "schema"  # reflected table structure of the target databases


class Cache(ABC):
    @abstractmethod
    async def get(self, key: str) -> str | None:
        """The cached text, or None on a miss *or on any failure*."""

    @abstractmethod
    async def set(self, key: str, value: str, ttl_s: int) -> None:
        """Store `value` for `ttl_s` seconds. Failures are swallowed."""

    @abstractmethod
    async def version(self, family: str) -> int | None:
        """Current version of a cache family, or None if it can't be determined (in which
        case the caller must skip the cache entirely for this request)."""

    @abstractmethod
    async def bump(self, family: str) -> None:
        """Invalidate every entry of a family, immediately."""

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class NullCache(Cache):
    """No caching: every lookup is a miss and `version` says "can't tell", so callers skip it."""

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        return None

    async def version(self, family: str) -> int | None:
        return None

    async def bump(self, family: str) -> None:
        return None

    async def ping(self) -> bool:
        return False

    async def close(self) -> None:
        return None
