"""Caches what it costs the most to learn about a target database: its table structure.

Reflecting a schema means several catalog queries per table, and `get_relationships` does it
for every table a caller can see. This wrapper caches `list_tables` and `describe_table`
(and nothing else; queries always go to the database).

Why this can't leak one caller's view to another: what is cached is the *raw* structure of
the database, the same for everybody. Each caller's permissions are applied afterwards, on
every request, by the service layer. Nothing about who asked ever goes into the cache.

Entries are keyed by the connection and its `updated_at`, so editing a connection in the GUI
starts a fresh set. The `schema` version can also be bumped to force a re-read after the
target database's structure changes.
"""

from pydantic import TypeAdapter

from mcp_sql_server.adapters.base import DBAdapter
from mcp_sql_server.cache.base import SCHEMA, Cache
from mcp_sql_server.cache.helpers import cached_json
from mcp_sql_server.models import ExplainResult, RawResult, TableDescription, TableInfo

_TABLES = TypeAdapter(list[TableInfo])
_DESCRIPTION = TypeAdapter(TableDescription)


class CachedAdapter(DBAdapter):
    def __init__(self, inner: DBAdapter, cache: Cache, namespace: str, ttl_s: int = 300) -> None:
        """`namespace` identifies this connection's current settings, e.g. '<id>:<updated_at>'."""
        self._inner = inner
        self._cache = cache
        self._namespace = namespace
        self._ttl_s = ttl_s

    @property
    def dialect_name(self) -> str:
        return self._inner.dialect_name

    @property
    def default_schema(self) -> str | None:
        return self._inner.default_schema

    async def connect(self) -> None:
        await self._inner.connect()

    async def close(self) -> None:
        await self._inner.close()

    async def list_tables(self) -> list[TableInfo]:
        return await cached_json(
            self._cache,
            SCHEMA,
            f"{self._namespace}:tables",
            self._ttl_s,
            _TABLES,
            self._inner.list_tables,
        )

    async def describe_table(self, table: str) -> TableDescription:
        # Errors (unknown table) are raised by the loader and therefore never cached.
        return await cached_json(
            self._cache,
            SCHEMA,
            f"{self._namespace}:table:{table.casefold()}",
            self._ttl_s,
            _DESCRIPTION,
            lambda: self._inner.describe_table(table),
        )

    # Everything below touches data or the database's live state, so it is never cached.

    async def execute(self, sql: str, *, max_rows: int, timeout_s: float) -> RawResult:
        return await self._inner.execute(sql, max_rows=max_rows, timeout_s=timeout_s)

    async def explain(self, sql: str, *, timeout_s: float) -> ExplainResult:
        return await self._inner.explain(sql, timeout_s=timeout_s)

    async def sample_rows(self, table: str, n: int, *, timeout_s: float) -> RawResult:
        return await self._inner.sample_rows(table, n, timeout_s=timeout_s)
