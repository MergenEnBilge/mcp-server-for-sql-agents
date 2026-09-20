"""Caches the permission and description lookups that happen on every single request.

Wraps any `MetaStore`. What is cached, and why it is safe:

  * Permission answers (which connections, tables and tools) are keyed by the caller's
    *subjects* (their user id plus roles), so one person's cached answer is only ever served
    to someone granted through exactly the same subjects.
  * They live for a short time AND are dropped instantly when the admin GUI bumps the `meta`
    version after any change, so revoking access is effective immediately.

What is deliberately NOT cached:
  * `get_connection`: it carries the (encrypted) database secret, which has no business being
    copied into Redis, and it is a single indexed lookup anyway.
  * The audit write: it must always reach the database.
  * Search: results depend on the keyword, and the query is index-backed.
"""

from collections.abc import Awaitable, Sequence
from uuid import UUID

from pydantic import TypeAdapter

from mcp_sql_server.cache.base import META, Cache
from mcp_sql_server.cache.helpers import cached_json, subjects_fingerprint
from mcp_sql_server.models import AuditEntry, ConnectionRecord, SchemaSearchHit
from mcp_sql_server.services.meta_store import DescriptionKey, MetaStore, Subjects

_RECORDS = TypeAdapter(list[ConnectionRecord])
_NAMES = TypeAdapter(list[str])
# Description keys are tuples, which JSON can't use as object keys, so store them as rows.
_DESCRIPTION_ROWS = TypeAdapter(list[tuple[str, str | None, str]])


class CachedMetaStore(MetaStore):
    def __init__(
        self,
        inner: MetaStore,
        cache: Cache,
        *,
        permission_ttl_s: int = 60,
        description_ttl_s: int = 300,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._permission_ttl_s = permission_ttl_s
        self._description_ttl_s = description_ttl_s

    async def list_connections(self, subjects: Subjects) -> list[ConnectionRecord]:
        return await cached_json(
            self._cache,
            META,
            f"connections:{subjects_fingerprint(subjects)}",
            self._permission_ttl_s,
            _RECORDS,
            lambda: self._inner.list_connections(subjects),
        )

    async def get_connection(self, name: str, subjects: Subjects) -> ConnectionRecord | None:
        return await self._inner.get_connection(name, subjects)

    async def allowed_tables(self, connection_id: UUID, subjects: Subjects) -> frozenset[str]:
        names = await cached_json(
            self._cache,
            META,
            f"tables:{connection_id}:{subjects_fingerprint(subjects)}",
            self._permission_ttl_s,
            _NAMES,
            lambda: _sorted(self._inner.allowed_tables(connection_id, subjects)),
        )
        return frozenset(names)

    async def allowed_tools(self, subjects: Subjects) -> frozenset[str]:
        names = await cached_json(
            self._cache,
            META,
            f"tools:{subjects_fingerprint(subjects)}",
            self._permission_ttl_s,
            _NAMES,
            lambda: _sorted(self._inner.allowed_tools(subjects)),
        )
        return frozenset(names)

    async def descriptions(self, connection_id: UUID) -> dict[DescriptionKey, str]:
        async def load() -> list[tuple[str, str | None, str]]:
            found = await self._inner.descriptions(connection_id)
            return [(table, column, text) for (table, column), text in found.items()]

        rows = await cached_json(
            self._cache,
            META,
            f"descriptions:{connection_id}",
            self._description_ttl_s,
            _DESCRIPTION_ROWS,
            load,
        )
        return {(table, column): text for table, column, text in rows}

    async def search_descriptions(
        self, connection_id: UUID, keyword: str, tables: Sequence[str], limit: int
    ) -> list[SchemaSearchHit]:
        return await self._inner.search_descriptions(connection_id, keyword, tables, limit)

    async def write_audit(self, entry: AuditEntry) -> None:
        await self._inner.write_audit(entry)

    async def close(self) -> None:
        await self._inner.close()
        await self._cache.close()


async def _sorted(pending: Awaitable[frozenset[str]]) -> list[str]:
    """Await a set of names and return it as a sorted list (JSON has no sets)."""
    return sorted(await pending)
