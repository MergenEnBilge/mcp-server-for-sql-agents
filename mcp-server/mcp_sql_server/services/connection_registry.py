"""Turns a row of app_meta.connections into a live database adapter.

Registering a database is configuration, not code: an admin picks an engine and
fills in host, database and credentials, and this module builds the connection
URL from that record. The password is decrypted here, in memory, only at the
moment a connection is opened. It is never logged and never leaves this module.

Adapters (and their connection pools) are kept for reuse. That's a cache of
open connections, not session state: any replica can build its own from the
same record. Each record carries `updated_at`, so when an admin edits a
connection the old pool is dropped and a fresh one is built on the next request.

The `details` JSON on a record looks like this (no secrets in here):

    {"host": "db.internal", "port": 5432, "database": "shop", "username": "reader",
     "options": {"sslmode": "require"}, "schemas": ["sales"]}

or, for SQLite: {"path": "/data/shop.sqlite"}
"""

import asyncio
import importlib.util
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.engine import URL

from mcp_sql_server.adapters.base import DBAdapter
from mcp_sql_server.adapters.sqlalchemy_adapter import SQLAlchemyAdapter
from mcp_sql_server.cache.base import Cache
from mcp_sql_server.cache.cached_adapter import CachedAdapter
from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.errors import ConnectionUnavailable
from mcp_sql_server.models import ConnectionRecord
from mcp_sql_server.services.connection_guard import check_host, resolve_sqlite_path

logger = logging.getLogger(__name__)

# Engine type (what an admin picks in the GUI) -> async driver used underneath.
# Supporting another SQLAlchemy engine is one line here plus installing its driver.
DRIVERS = {
    "postgresql": "asyncpg",
    "mysql": "aiomysql",
    "mssql": "aioodbc",
    "sqlite": "aiosqlite",
}


# The driver Microsoft's ODBC layer needs for SQL Server, when the engine doesn't say otherwise.
MSSQL_DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"


def driver_problem(engine: str) -> str | None:
    """Why this server can't open databases of this engine, or None if it can.

    A registered database of an engine whose driver isn't installed would only ever fail, so the
    admin console says so up front instead of letting someone find out at the first query.
    """
    module = DRIVERS.get(engine)
    if module is None:
        return "This engine isn't supported."
    if importlib.util.find_spec(module) is None:
        return (
            f"The '{module}' driver isn't installed in this server. "
            "Rebuild the image with this engine included (see the README)."
        )
    if engine == "mssql":
        try:
            pyodbc = importlib.import_module("pyodbc")  # no type stubs; only asked for its drivers
        except ImportError:
            return "The 'pyodbc' package isn't installed in this server."
        if not any("SQL Server" in name for name in pyodbc.drivers()):
            return (
                "Microsoft's ODBC driver for SQL Server isn't installed in this server. "
                "Rebuild the image with mssql included (see the README)."
            )
    return None


class AdapterProvider(Protocol):
    """What the services need from the registry. Tests substitute their own."""

    async def adapter_for(self, record: ConnectionRecord) -> DBAdapter: ...


def build_engine_url(
    record: ConnectionRecord, password: str | None, sqlite_root: str | None = None
) -> URL:
    """The SQLAlchemy URL for a record. Raises ValueError for one that must not be opened
    (see connection_guard.py): `sqlite_root` is the only folder SQLite files may live in."""
    driver = DRIVERS.get(record.engine)
    if driver is None:
        raise ValueError(f"unsupported engine {record.engine!r}; supported: {sorted(DRIVERS)}")
    details = record.details
    if record.engine == "sqlite":
        path = resolve_sqlite_path(str(details["path"]), sqlite_root)
        return URL.create(f"sqlite+{driver}", database=str(path))
    if details.get("host"):
        check_host(str(details["host"]))
    query = dict(details.get("options") or {})
    if record.engine == "mssql":
        query.setdefault("driver", MSSQL_DEFAULT_ODBC_DRIVER)  # the ODBC layer can't guess one
    return URL.create(
        f"{record.engine}+{driver}",
        username=details.get("username"),
        password=password,
        host=details.get("host"),
        port=details.get("port"),
        database=details.get("database"),
        query=query,
    )


AdapterFactory = Callable[..., DBAdapter]


class ConnectionRegistry:
    def __init__(
        self,
        secret_box: SecretBox,
        adapter_factory: AdapterFactory = SQLAlchemyAdapter,
        cache: Cache | None = None,
        schema_ttl_s: int = 300,
        sqlite_root: str | None = None,
    ) -> None:
        self._secret_box = secret_box
        self._sqlite_root = sqlite_root
        self._adapter_factory = adapter_factory
        self._cache = cache
        self._schema_ttl_s = schema_ttl_s
        self._adapters: dict[UUID, tuple[datetime, DBAdapter]] = {}
        self._lock = asyncio.Lock()

    async def adapter_for(self, record: ConnectionRecord) -> DBAdapter:
        async with self._lock:
            cached = self._adapters.get(record.id)
            if cached is not None:
                version, adapter = cached
                if version == record.updated_at:
                    return adapter
                await adapter.close()  # the record was edited: drop the old pool
                del self._adapters[record.id]

            adapter = await self._open(record)
            self._adapters[record.id] = (record.updated_at, adapter)
            return adapter

    async def _open(self, record: ConnectionRecord) -> DBAdapter:
        try:
            password = (
                self._secret_box.decrypt(record.secret_encrypted)
                if record.secret_encrypted
                else None
            )
            adapter = self._adapter_factory(
                build_engine_url(record, password, self._sqlite_root),
                schemas=_schemas(record.details),
            )
            await adapter.connect()
            if self._cache is not None:
                # Cache table structure per connection *and* per edit of it: changing a
                # connection in the GUI (new updated_at) starts a fresh set of entries.
                namespace = f"{record.id}:{record.updated_at.timestamp()}"
                adapter = CachedAdapter(adapter, self._cache, namespace, self._schema_ttl_s)
        except Exception:
            # The real reason (bad host, wrong password, ...) is for operators, not the LLM.
            # Note: the URL, which contains the password, is deliberately not logged.
            logger.exception("could not open connection %r (engine %s)", record.name, record.engine)
            raise ConnectionUnavailable(
                f"The connection '{record.name}' is currently unavailable. "
                "Contact an administrator."
            ) from None
        return adapter

    async def close(self) -> None:
        async with self._lock:
            for _version, adapter in self._adapters.values():
                await adapter.close()
            self._adapters.clear()


def _schemas(details: dict[str, Any]) -> list[str] | None:
    schemas = details.get("schemas")
    return list(schemas) if schemas else None
