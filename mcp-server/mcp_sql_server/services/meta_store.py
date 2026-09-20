"""Reads and writes the app's own data in app_meta (always Postgres).

`MetaStore` is the interface the services use. Keeping it an interface means the
authorization logic can be unit-tested with an in-memory fake, and the Redis
caching added later slots in as a wrapper without the services noticing.

The store answers factual questions ("which tables are granted to these
subjects?"). Deciding what to do with the answer belongs to the services.
Every lookup is keyed by the caller's *subjects* (their user id plus their
roles), which is also what a cache must be keyed by so one person's cached
result is never served to someone with different access.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ARRAY, Text, bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mcp_sql_server.models import AuditEntry, ConnectionRecord, SchemaSearchHit

Subjects = Sequence[tuple[str, str]]  # ("user", "<sub>") / ("role", "<name>")

# Description lookups are keyed by (table, column) with both lower-cased; column is None for
# the table's own description.
DescriptionKey = tuple[str, str | None]


class MetaStore(ABC):
    @abstractmethod
    async def list_connections(self, subjects: Subjects) -> list[ConnectionRecord]:
        """Active connections these subjects may use. `secret_encrypted` is left empty."""

    @abstractmethod
    async def get_connection(self, name: str, subjects: Subjects) -> ConnectionRecord | None:
        """One active connection by name, including its secret, or None if it doesn't exist
        or these subjects may not use it."""

    @abstractmethod
    async def allowed_tables(self, connection_id: UUID, subjects: Subjects) -> frozenset[str]:
        """Table names granted to any of these subjects on this connection (as stored)."""

    @abstractmethod
    async def allowed_tools(self, subjects: Subjects) -> frozenset[str]:
        """MCP tool names granted to any of these subjects."""

    @abstractmethod
    async def descriptions(self, connection_id: UUID) -> dict[DescriptionKey, str]:
        """Curated, non-empty descriptions for a connection's tables and columns."""

    @abstractmethod
    async def search_descriptions(
        self, connection_id: UUID, keyword: str, tables: Sequence[str], limit: int
    ) -> list[SchemaSearchHit]:
        """Keyword search over names and descriptions, restricted to `tables` (lower-cased)."""

    @abstractmethod
    async def write_audit(self, entry: AuditEntry) -> None:
        """Append one row to the audit log."""

    @abstractmethod
    async def close(self) -> None: ...


_SUBJECTS = bindparam("types", type_=ARRAY(Text)), bindparam("ids", type_=ARRAY(Text))

# Matches a row's (subject_type, subject_id) against any of the caller's subjects.
_MATCHES_A_SUBJECT = (
    "JOIN unnest(CAST(:types AS text[]), CAST(:ids AS text[])) AS s(t, i)"
    " ON s.t = {alias}.subject_type AND s.i = {alias}.subject_id"
)

_CONNECTION_COLUMNS = "c.id, c.name, c.engine, c.description, c.details, c.updated_at"

_LIST_CONNECTIONS = f"""
    SELECT {_CONNECTION_COLUMNS}, NULL AS secret_encrypted
    FROM connections c
    WHERE c.is_active AND EXISTS (
        SELECT 1 FROM connection_access a {_MATCHES_A_SUBJECT.format(alias="a")}
        WHERE a.connection_id = c.id)
    ORDER BY c.name
"""

_GET_CONNECTION = f"""
    SELECT {_CONNECTION_COLUMNS}, c.secret_encrypted
    FROM connections c
    WHERE c.name = :name AND c.is_active AND EXISTS (
        SELECT 1 FROM connection_access a {_MATCHES_A_SUBJECT.format(alias="a")}
        WHERE a.connection_id = c.id)
"""

_ALLOWED_TABLES = f"""
    SELECT DISTINCT p.table_name
    FROM table_permissions p {_MATCHES_A_SUBJECT.format(alias="p")}
    WHERE p.connection_id = :connection_id
"""

_ALLOWED_TOOLS = f"""
    SELECT DISTINCT p.tool_name
    FROM tool_permissions p {_MATCHES_A_SUBJECT.format(alias="p")}
"""

_DESCRIPTIONS = """
    SELECT lower(table_name), lower(column_name), description
    FROM schema_descriptions
    WHERE connection_id = :connection_id AND description <> ''
"""

# search_schema v1: Postgres full-text search on the stored tsvector, plus trigram similarity
# and substring matching on names so typos and partial words still hit. This keeps working
# whichever engine the *target* database is, because it only reads app_meta.
# A vector-embedding upgrade would add an embedding column to schema_descriptions and rank
# by cosine distance here, alongside (or instead of) ts_rank.
_SEARCH = """
    WITH q AS (SELECT websearch_to_tsquery('english', :keyword) AS tsq)
    SELECT table_name, column_name, description,
           CAST(ts_rank(search_vector, q.tsq)
                + greatest(similarity(table_name, :keyword),
                           similarity(coalesce(column_name, ''), :keyword)) AS float8) AS score
    FROM schema_descriptions, q
    WHERE connection_id = :connection_id
      AND lower(table_name) = ANY(:tables)
      AND (search_vector @@ q.tsq
           OR table_name % :keyword OR column_name % :keyword
           OR table_name ILIKE :pattern OR column_name ILIKE :pattern)
    ORDER BY score DESC, table_name, column_name NULLS FIRST
    LIMIT :limit
"""

_WRITE_AUDIT = """
    INSERT INTO audit_log (caller_sub, caller_name, tool_name, connection_name, tables,
                           arguments, success, error_message, row_count, result_summary,
                           duration_ms)
    VALUES (:caller_sub, :caller_name, :tool_name, :connection_name, :tables,
            :arguments, :success, :error_message, :row_count, :result_summary, :duration_ms)
"""


class PostgresMetaStore(MetaStore):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    def from_url(cls, url: str, *, pool_size: int = 5) -> "PostgresMetaStore":
        return cls(create_async_engine(url, pool_size=pool_size, pool_pre_ping=True))

    async def close(self) -> None:
        await self._engine.dispose()

    @staticmethod
    def _subject_params(subjects: Subjects) -> dict[str, list[str]]:
        return {"types": [t for t, _ in subjects], "ids": [i for _, i in subjects]}

    async def list_connections(self, subjects: Subjects) -> list[ConnectionRecord]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(_LIST_CONNECTIONS).bindparams(*_SUBJECTS), self._subject_params(subjects)
            )
            return [ConnectionRecord(**row) for row in result.mappings()]

    async def get_connection(self, name: str, subjects: Subjects) -> ConnectionRecord | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(_GET_CONNECTION).bindparams(*_SUBJECTS),
                {"name": name, **self._subject_params(subjects)},
            )
            row = result.mappings().first()
            return ConnectionRecord(**row) if row else None

    async def allowed_tables(self, connection_id: UUID, subjects: Subjects) -> frozenset[str]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(_ALLOWED_TABLES).bindparams(*_SUBJECTS),
                {"connection_id": connection_id, **self._subject_params(subjects)},
            )
            return frozenset(row[0] for row in result)

    async def allowed_tools(self, subjects: Subjects) -> frozenset[str]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(_ALLOWED_TOOLS).bindparams(*_SUBJECTS), self._subject_params(subjects)
            )
            return frozenset(row[0] for row in result)

    async def descriptions(self, connection_id: UUID) -> dict[DescriptionKey, str]:
        async with self._engine.connect() as conn:
            result = await conn.execute(text(_DESCRIPTIONS), {"connection_id": connection_id})
            return {(table, column): description for table, column, description in result}

    async def search_descriptions(
        self, connection_id: UUID, keyword: str, tables: Sequence[str], limit: int
    ) -> list[SchemaSearchHit]:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = text(_SEARCH).bindparams(bindparam("tables", type_=ARRAY(Text)))
        async with self._engine.connect() as conn:
            result = await conn.execute(
                statement,
                {
                    "connection_id": connection_id,
                    "keyword": keyword,
                    "pattern": f"%{escaped}%",
                    "tables": list(tables),
                    "limit": limit,
                },
            )
            return [
                SchemaSearchHit(
                    table=row["table_name"],
                    column=row["column_name"],
                    description=row["description"],
                    score=row["score"],
                )
                for row in result.mappings()
            ]

    async def write_audit(self, entry: AuditEntry) -> None:
        statement = text(_WRITE_AUDIT).bindparams(
            bindparam("tables", type_=ARRAY(Text)), bindparam("arguments", type_=JSONB)
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                statement,
                {
                    "caller_sub": entry.caller_sub,
                    "caller_name": entry.caller_name,
                    "tool_name": entry.tool_name,
                    "connection_name": entry.connection_name,
                    "tables": entry.tables,
                    "arguments": entry.arguments,
                    "success": entry.success,
                    "error_message": entry.error_message,
                    "row_count": entry.row_count,
                    "result_summary": entry.result_summary,
                    "duration_ms": entry.duration_ms,
                },
            )
