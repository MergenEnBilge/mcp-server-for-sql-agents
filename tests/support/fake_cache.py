"""In-memory caches for tests: one that works (with expiry), one that always fails."""

from mcp_sql_server.cache.base import Cache


class MemoryCache(Cache):
    def __init__(self) -> None:
        self.data: dict[str, tuple[str, float]] = {}
        self.versions: dict[str, int] = {}
        self.now = 0.0  # tests move the clock by assigning to this
        self.gets = 0
        self.sets = 0
        self.counters: dict[str, tuple[int, float]] = {}

    async def get(self, key: str) -> str | None:
        self.gets += 1
        entry = self.data.get(key)
        if entry is None or entry[1] <= self.now:
            return None
        return entry[0]

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        self.sets += 1
        self.data[key] = (value, self.now + ttl_s)

    async def version(self, family: str) -> int | None:
        return self.versions.get(family, 0)

    async def bump(self, family: str) -> None:
        self.versions[family] = self.versions.get(family, 0) + 1

    async def count(self, key: str, ttl_s: int) -> int | None:
        value, expires = self.counters.get(key, (0, self.now + ttl_s))
        if expires <= self.now:
            value, expires = 0, self.now + ttl_s
        self.counters[key] = (value + 1, expires)
        return value + 1

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class BrokenCache(Cache):
    """What a caller sees when Redis is down: every lookup misses, the version is unknown."""

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        return None

    async def version(self, family: str) -> int | None:
        return None

    async def bump(self, family: str) -> None:
        return None

    async def count(self, key: str, ttl_s: int) -> int | None:
        return None

    async def ping(self) -> bool:
        return False

    async def close(self) -> None:
        pass
