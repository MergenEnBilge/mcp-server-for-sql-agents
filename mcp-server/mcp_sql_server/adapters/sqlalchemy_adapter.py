"""A DBAdapter for every engine SQLAlchemy supports, driven purely by a connection URL.

Which database this talks to is configuration, not code: hand it
`postgresql+asyncpg://...`, `mysql+aiomysql://...`, `mssql+aioodbc://...` or
`sqlite+aiosqlite:///...` and it works. Schema reflection goes through
SQLAlchemy's `inspect()`, so there is no per-engine information_schema SQL.

Where engines really do differ, the differences are handled explicitly and are
listed here in one place:

                 read-only enforcement            time limit                    EXPLAIN
  PostgreSQL     read-only transaction per        statement_timeout, per        EXPLAIN (shows cost)
                 query + the DB role itself      transaction
  SQLite         opened with mode=ro plus         progress handler that aborts  EXPLAIN QUERY PLAN
                 PRAGMA query_only                the query at the deadline
  MySQL/MariaDB  read-only session                max_execution_time /          EXPLAIN
                                                  max_statement_time
  SQL Server     the DB role only (SQL Server     client-side timeout only      not supported
                 has no read-only session)

Only PostgreSQL and SQLite are exercised by the automated tests. The MySQL and
SQL Server branches follow each driver's documented behaviour but have not been
run against a live server yet.

Row caps: the statement is streamed and we stop reading after `max_rows + 1`
rows. That works for any SQL on any engine. Wrapping the caller's SQL in
`SELECT * FROM (...) LIMIT n` would break on SQL Server (ORDER BY and CTEs inside
a subquery) and on MySQL (duplicate column names in a derived table). Where we
build the SQL ourselves (sample rows) we use SQLAlchemy's `.limit()`, so
LIMIT / TOP / FETCH FIRST is always the right one for the engine.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from sqlalchemy import event, inspect, literal_column, select, text
from sqlalchemy import table as sa_table
from sqlalchemy.engine import URL, Inspector, make_url
from sqlalchemy.exc import DBAPIError, ResourceClosedError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql.base import Executable

from mcp_sql_server.adapters.base import DBAdapter
from mcp_sql_server.errors import ExplainNotSupported, QueryFailed, QueryTimeout, TableNotFound
from mcp_sql_server.models import (
    ColumnInfo,
    ExplainResult,
    RawResult,
    Relationship,
    TableDescription,
    TableInfo,
    TableKind,
)

# Client-side safety net on top of the server-side limit, for engines that have one.
_CLIENT_TIMEOUT_GRACE_S = 1.0
_MAX_ERROR_CHARS = 300


class SQLAlchemyAdapter(DBAdapter):
    def __init__(
        self,
        engine_url: str | URL,
        *,
        schemas: Sequence[str] | None = None,
        pool_size: int = 5,
    ) -> None:
        """
        `schemas` lists extra schemas to expose besides the default one. Tables in
        those are named `schema.table` everywhere.
        """
        url = make_url(engine_url)
        self._dialect = url.get_backend_name()
        self._url = _read_only_sqlite_url(url) if self._dialect == "sqlite" else url
        self._schemas: list[str | None] = [None, *schemas] if schemas else [None]
        self._pool_size = pool_size
        self._engine: AsyncEngine | None = None
        self._default_schema: str | None = None

    # --- lifecycle ------------------------------------------------------------------------

    @property
    def dialect_name(self) -> str:
        return self._dialect

    @property
    def default_schema(self) -> str | None:
        return self._default_schema

    async def connect(self) -> None:
        engine = create_async_engine(self._url, pool_size=self._pool_size, pool_pre_ping=True)
        event.listen(engine.sync_engine, "connect", self._make_connection_readonly)
        try:
            async with engine.connect() as conn:
                self._default_schema = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).default_schema_name
                )
        except BaseException:
            await engine.dispose()
            raise
        self._engine = engine

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    def _require_engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("adapter is not connected; call connect() first")
        return self._engine

    def _make_connection_readonly(self, dbapi_connection: Any, _record: Any) -> None:
        """Runs once for each new pooled connection."""
        statement = {
            "sqlite": "PRAGMA query_only = ON",
            "mysql": "SET SESSION TRANSACTION READ ONLY",
        }.get(self._dialect)
        if statement is None:
            return
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(statement)
        finally:
            cursor.close()

    # --- schema discovery -----------------------------------------------------------------

    async def list_tables(self) -> list[TableInfo]:
        return [
            TableInfo(name=name, kind=kind, comment=comment)
            for name, (_schema, _table, kind, comment) in (await self._table_index()).items()
        ]

    async def describe_table(self, table: str) -> TableDescription:
        schema, name, kind, comment = await self._lookup(table)

        def reflect(inspector: Inspector) -> TableDescription:
            columns = inspector.get_columns(name, schema=schema)
            pk_columns: set[str] = set()
            foreign_keys: list[Relationship] = []
            if kind == "table":
                pk = inspector.get_pk_constraint(name, schema=schema)
                pk_columns = set(pk.get("constrained_columns") or [])
                for fk in inspector.get_foreign_keys(name, schema=schema):
                    foreign_keys.append(
                        Relationship(
                            from_table=self._canonical(schema, name),
                            columns=tuple(fk["constrained_columns"]),
                            to_table=self._canonical(
                                fk.get("referred_schema"), fk["referred_table"]
                            ),
                            to_columns=tuple(fk["referred_columns"]),
                        )
                    )
            return TableDescription(
                name=self._canonical(schema, name),
                kind=kind,
                comment=comment,
                columns=tuple(
                    ColumnInfo(
                        name=col["name"],
                        type=self._type_name(col["type"]),
                        # A primary key is never null. SQLite doesn't say so for a bare
                        # `INTEGER PRIMARY KEY`, so don't trust its report for those columns.
                        nullable=bool(col.get("nullable", True)) and col["name"] not in pk_columns,
                        primary_key=col["name"] in pk_columns,
                        comment=col.get("comment"),
                    )
                    for col in columns
                ),
                foreign_keys=tuple(foreign_keys),
            )

        return await self._reflect(reflect)

    def _canonical(self, schema: str | None, name: str) -> str:
        return name if schema is None or schema == self._default_schema else f"{schema}.{name}"

    def _type_name(self, column_type: Any) -> str:
        try:
            return str(column_type.compile(dialect=self._require_engine().dialect))
        except Exception:  # exotic reflected types: fall back to whatever SQLAlchemy prints
            return str(column_type)

    async def _reflect[T](self, work: Callable[[Inspector], T]) -> T:
        try:
            async with self._require_engine().connect() as conn:
                return await conn.run_sync(lambda sync_conn: work(inspect(sync_conn)))
        except Exception as exc:
            driver_error = _driver_error(exc)
            if driver_error is None:
                raise
            raise self._translate(driver_error, timeout_s=0, deadline=None) from exc

    async def _table_index(
        self,
    ) -> dict[str, tuple[str | None, str, TableKind, str | None]]:
        """canonical name -> (schema, table name, kind, comment) for everything exposed."""

        def reflect(
            inspector: Inspector,
        ) -> dict[str, tuple[str | None, str, TableKind, str | None]]:
            index: dict[str, tuple[str | None, str, TableKind, str | None]] = {}
            for schema in self._schemas:
                comments = self._table_comments(inspector, schema)
                for kind, names in (
                    ("table", inspector.get_table_names(schema=schema)),
                    ("view", inspector.get_view_names(schema=schema)),
                ):
                    for name in names:
                        index[self._canonical(schema, name)] = (
                            schema,
                            name,
                            kind,  # type: ignore[assignment]  # literal narrowed by the tuple above
                            comments.get(name),
                        )
            # Engines list tables in different orders (Postgres by catalog order, SQLite
            # alphabetically). Sort so callers see the same thing whatever the engine.
            return dict(sorted(index.items(), key=lambda item: item[0].casefold()))

        return await self._reflect(reflect)

    @staticmethod
    def _table_comments(inspector: Inspector, schema: str | None) -> dict[str, str | None]:
        try:
            multi = inspector.get_multi_table_comment(schema=schema)
        except NotImplementedError:  # SQLite and SQL Server don't expose table comments
            return {}
        return {name: info.get("text") for (_schema, name), info in multi.items()}

    async def _lookup(self, table: str) -> tuple[str | None, str, TableKind, str | None]:
        """Find a table by name: exact match first, then a case-insensitive one if unambiguous."""
        index = await self._table_index()
        if table in index:
            return index[table]
        matches = [entry for name, entry in index.items() if name.casefold() == table.casefold()]
        if len(matches) == 1:
            return matches[0]
        raise TableNotFound(f"Table '{table}' was not found or is not available to you.")

    # --- running SQL ----------------------------------------------------------------------

    async def execute(self, sql: str, *, max_rows: int, timeout_s: float) -> RawResult:
        return await self._run(_literal_sql(sql), max_rows=max_rows, timeout_s=timeout_s)

    async def sample_rows(self, table: str, n: int, *, timeout_s: float) -> RawResult:
        schema, name, _kind, _comment = await self._lookup(table)
        statement: Executable = (
            select(literal_column("*")).select_from(sa_table(name, schema=schema)).limit(int(n))
        )
        return await self._run(statement, max_rows=int(n), timeout_s=timeout_s)

    async def explain(self, sql: str, *, timeout_s: float) -> ExplainResult:
        if self._dialect == "mssql":
            raise ExplainNotSupported(
                "Query plans are not available for SQL Server connections. "
                "Use run_query with a small row_limit to sanity-check the query instead."
            )
        prefix = "EXPLAIN QUERY PLAN " if self._dialect == "sqlite" else "EXPLAIN "
        raw = await self._run(_literal_sql(prefix + sql), max_rows=1000, timeout_s=timeout_s)

        if self._dialect == "sqlite":  # rows are (id, parent, notused, detail)
            plan = "\n".join(str(row[-1]) for row in raw.rows)
        elif len(raw.columns) == 1:  # PostgreSQL: one text line per row
            plan = "\n".join(str(row[0]) for row in raw.rows)
        else:  # MySQL/MariaDB: a small table
            plan = "\n".join(
                ", ".join(f"{col}={val}" for col, val in zip(raw.columns, row, strict=True))
                for row in raw.rows
            )

        cost = rows = None
        if self._dialect == "postgresql":
            match = re.search(r"cost=[\d.]+\.\.([\d.]+) rows=(\d+)", plan)
            if match:
                cost, rows = float(match.group(1)), int(match.group(2))
        return ExplainResult(plan=plan, estimated_cost=cost, estimated_rows=rows)

    async def _run(self, statement: Executable, *, max_rows: int, timeout_s: float) -> RawResult:
        async with self._query_connection(timeout_s) as conn:
            result = await conn.stream(statement)
            try:
                columns = list(result.keys())
            except ResourceClosedError:  # statement returned no rows (e.g. a SET)
                return RawResult(columns=[], rows=[], truncated=False)
            rows = await result.fetchmany(max_rows + 1)
            await result.close()
        return RawResult(
            columns=columns,
            rows=[tuple(row) for row in rows[:max_rows]],
            truncated=len(rows) > max_rows,
        )

    @asynccontextmanager
    async def _query_connection(self, timeout_s: float) -> AsyncIterator[AsyncConnection]:
        """A connection set up for one read-only query with a time limit.

        Also turns driver errors into our own errors, so nothing above this layer
        needs to know which driver is underneath.
        """
        deadline = time.monotonic() + timeout_s
        server_enforced = self._dialect in ("postgresql", "sqlite", "mysql")
        client_timeout = timeout_s + (_CLIENT_TIMEOUT_GRACE_S if server_enforced else 0)
        cleanup: Callable[[], Any] | None = None
        try:
            async with self._require_engine().connect() as conn:
                if self._dialect == "postgresql":
                    conn = await conn.execution_options(postgresql_readonly=True)
                    # A SELECT (rather than SET LOCAL) so the transaction has already run a
                    # query: Postgres then refuses any later attempt to switch it to read-write.
                    await conn.exec_driver_sql(
                        "SELECT set_config('statement_timeout', "
                        f"'{max(1, int(timeout_s * 1000))}', true)"
                    )
                elif self._dialect == "mysql":
                    await self._set_mysql_time_limit(conn, timeout_s)
                elif self._dialect == "sqlite":
                    cleanup = await self._set_sqlite_deadline(conn, deadline)
                try:
                    async with asyncio.timeout(client_timeout):
                        yield conn
                finally:
                    if cleanup is not None:
                        with suppress(Exception):
                            await cleanup()
        except TimeoutError:
            raise QueryTimeout(f"The query exceeded the {timeout_s:g}s time limit.") from None
        except Exception as exc:
            driver_error = _driver_error(exc)
            if driver_error is None:
                raise
            raise self._translate(driver_error, timeout_s=timeout_s, deadline=deadline) from exc

    async def _set_mysql_time_limit(self, conn: AsyncConnection, timeout_s: float) -> None:
        if getattr(conn.dialect, "is_mariadb", False):
            await conn.exec_driver_sql(f"SET SESSION max_statement_time = {timeout_s:g}")
        else:
            await conn.exec_driver_sql(
                f"SET SESSION max_execution_time = {max(1, int(timeout_s * 1000))}"
            )

    async def _set_sqlite_deadline(
        self, conn: AsyncConnection, deadline: float
    ) -> Callable[[], Any]:
        """SQLite has no statement timeout, so ask it to check the clock every 1000 VM steps
        and abort once the deadline has passed. Returns a coroutine function that removes it."""
        raw = await conn.get_raw_connection()
        driver_connection = raw.driver_connection
        assert driver_connection is not None
        await driver_connection.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 1000
        )
        return lambda: driver_connection.set_progress_handler(None, 0)

    def _translate(
        self, error: BaseException, *, timeout_s: float, deadline: float | None
    ) -> QueryTimeout | QueryFailed:
        message = _first_line(str(error))
        lowered = message.lower()
        timed_out = (
            "statement timeout" in lowered  # PostgreSQL
            or "maximum statement execution time" in lowered  # MySQL / MariaDB
            or (
                self._dialect == "sqlite"
                and deadline is not None
                and "interrupted" in lowered
                and time.monotonic() >= deadline
            )
        )
        if timed_out:
            return QueryTimeout(f"The query exceeded the {timeout_s:g}s time limit.")
        return QueryFailed(message)


# SQLAlchemy wraps most driver errors in DBAPIError, but not those raised while reading rows
# from a server-side cursor (asyncpg in particular). We recognise both.
_DRIVER_MODULES = {"asyncpg", "aiosqlite", "sqlite3", "aiomysql", "pymysql", "aioodbc", "pyodbc"}


def _driver_error(exc: Exception) -> BaseException | None:
    """The underlying database-driver error if `exc` is one (or wraps one), else None."""
    if isinstance(exc, DBAPIError):
        return exc.orig if exc.orig is not None else exc
    if type(exc).__module__.split(".")[0] in _DRIVER_MODULES:
        return exc
    return None


def _read_only_sqlite_url(url: URL) -> URL:
    """Reopen a SQLite file in read-only mode (SQLite has no users or roles to lock down)."""
    path = url.database
    if not path or path == ":memory:":
        return url  # an in-memory database can't be opened read-only; only useful in tests
    uri = path if path.startswith("file:") else Path(path).resolve().as_uri()
    return url.set(database=uri, query={**url.query, "mode": "ro", "uri": "true"})


def _literal_sql(sql: str) -> Executable:
    """Wrap raw SQL for execution without SQLAlchemy mistaking `:word` for a bind parameter
    (times like '12:30' and Postgres casts would otherwise break)."""
    return text(sql.replace(":", r"\:"))


def _first_line(message: str) -> str:
    """Driver errors often run to many lines and start with a class name; keep the useful part."""
    line = message.strip().splitlines()[0] if message.strip() else "database error"
    line = re.sub(r"^<class '[^']+'>:\s*", "", line)
    return line[:_MAX_ERROR_CHARS]
