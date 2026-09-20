"""The one caching pattern used everywhere: look up by version-stamped key, else load and store."""

import hashlib
import logging
from collections.abc import Awaitable, Callable, Iterable

from pydantic import TypeAdapter, ValidationError

from mcp_sql_server.cache.base import Cache

logger = logging.getLogger(__name__)


async def cached_json[T](
    cache: Cache,
    family: str,
    key: str,
    ttl_s: int,
    adapter: TypeAdapter[T],
    load: Callable[[], Awaitable[T]],
) -> T:
    """Return the cached value for `key` if there is one, otherwise `load()` it and cache it.

    The key is stamped with the family's current version, so bumping the version orphans
    every old entry. If the cache can't say what the version is, it is skipped entirely.
    """
    version = await cache.version(family)
    if version is None:
        return await load()

    full_key = f"{family}:{version}:{key}"
    hit = await cache.get(full_key)
    if hit is not None:
        try:
            return adapter.validate_json(hit)
        except ValidationError:
            # A shape that no longer matches (e.g. after an upgrade). Treat it as a miss.
            logger.warning("discarding unreadable cache entry %s", full_key)

    value = await load()
    await cache.set(full_key, adapter.dump_json(value).decode(), ttl_s)
    return value


def subjects_fingerprint(subjects: Iterable[tuple[str, str]]) -> str:
    """A stable short id for a caller's set of grants (their user id plus their roles).

    Permission lookups are cached under this, so two callers only ever share an entry if
    they are granted through exactly the same user id and roles.
    """
    joined = "\n".join(f"{kind}:{name}" for kind, name in sorted(subjects))
    return hashlib.sha256(joined.encode()).hexdigest()[:32]
