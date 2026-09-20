"""Who may use which tool, connection and table.

Three independent checks, all default-deny (a grant is a row; no row, no access):
  1. tool_permissions        may this caller call this tool at all?
  2. connection_access       may they use this database?
  3. table_permissions       which tables on it may they see?

Some answers are deliberately blurred. "That connection doesn't exist" and "you
may not use it" look identical to the caller, and so do a table that doesn't
exist and one they can't see. Otherwise the error messages would let someone map
out what's in the system without having access to it.
"""

from dataclasses import dataclass

from mcp_sql_server.errors import ConnectionNotFound, TableNotFound, ToolNotPermitted
from mcp_sql_server.models import Caller, ConnectionRecord
from mcp_sql_server.services.meta_store import MetaStore

TOOL_NAMES = frozenset(
    {
        "list_connections",
        "list_tables",
        "describe_table",
        "search_schema",
        "get_relationships",
        "run_query",
        "explain_query",
        "get_sample_rows",
    }
)


@dataclass(frozen=True, slots=True)
class ConnectionAccess:
    """A connection the caller may use, and the tables on it they may see."""

    record: ConnectionRecord
    tables: frozenset[str]  # lower-cased

    def allows(self, table: str) -> bool:
        # Names are compared case-insensitively. If a database has two tables that differ only
        # by case, a grant on one covers both; that's a rare setup and the safe direction to
        # be wrong in is documented rather than surprising.
        return table.casefold() in self.tables

    def require_table(self, table: str) -> None:
        if not self.allows(table):
            raise TableNotFound(f"Table '{table}' was not found or is not available to you.")


class PermissionService:
    def __init__(self, store: MetaStore) -> None:
        self._store = store

    async def require_tool(self, caller: Caller, tool: str) -> None:
        if tool not in await self._store.allowed_tools(caller.subjects()):
            raise ToolNotPermitted(f"You are not permitted to use the '{tool}' tool.")

    async def list_connections(self, caller: Caller) -> list[ConnectionRecord]:
        return await self._store.list_connections(caller.subjects())

    async def open_connection(
        self, caller: Caller, tool: str, connection_name: str
    ) -> ConnectionAccess:
        """Check the tool and the connection, and load the caller's tables on it."""
        await self.require_tool(caller, tool)
        record = await self._store.get_connection(connection_name, caller.subjects())
        if record is None:
            raise ConnectionNotFound(
                f"Connection '{connection_name}' was not found or is not available to you."
            )
        granted = await self._store.allowed_tables(record.id, caller.subjects())
        return ConnectionAccess(record=record, tables=frozenset(t.casefold() for t in granted))
