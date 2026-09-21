"""Who may use which tool, connection and table.

Two questions are asked of every call, and both must say yes:

  A. May this AI client (agent) do it at all?   agents, agent_connections
     An agent starts out unapproved. An administrator approves it and sets a ceiling: the tools
     it may call and the databases it may reach. Nothing else about the agent matters.
  B. May this person do it?                      three independent checks, default-deny
       1. tool_permissions        may they call this tool at all?
       2. connection_access       may they use this database?
       3. table_permissions       which tables on it may they see?

What an agent can do is therefore the overlap of A and B. An agent can never do more than the
person it is acting for, and a person never gets more than the agent they use was approved for.
A grant is always a row; no row, no access.

Some answers are deliberately blurred. "That connection doesn't exist" and "you
may not use it" look identical to the caller, and so do a table that doesn't
exist and one they can't see. Otherwise the error messages would let someone map
out what's in the system without having access to it.
"""

import logging
import re
import time
from dataclasses import dataclass

from mcp_sql_server.errors import (
    AgentNotApproved,
    ConnectionNotFound,
    TableNotFound,
    ToolNotPermitted,
)
from mcp_sql_server.models import AgentRecord, Caller, ConnectionRecord
from mcp_sql_server.services.meta_store import MetaStore

logger = logging.getLogger(__name__)

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

# Ready-made choices for an administrator approving an agent. "Schema only" lets it learn what
# exists without ever seeing a row of data.
SCHEMA_TOOLS = (
    "list_connections",
    "list_tables",
    "describe_table",
    "search_schema",
    "get_relationships",
)
TOOL_PRESETS = {
    "explorer": SCHEMA_TOOLS,
    "analyst": tuple(sorted(TOOL_NAMES)),
}

# How often "this agent was just seen" is written to the database. Every call would work, but it
# would turn each read into a write for no benefit.
_TOUCH_EVERY_S = 60.0

_MAX_NAME_CHARS = 100
_NOT_PRINTABLE = re.compile(r"[^\S ]|[\x00-\x1f\x7f]+")


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
        self._last_touched: dict[str, float] = {}

    # --- the agent -------------------------------------------------------------------------

    async def check_agent(self, caller: Caller) -> AgentRecord | None:
        """Refuse callers whose AI client isn't approved. Returns the client's record, or None
        for the stdio transport (no OAuth client; trusted by whoever configured it)."""
        if caller.client_id is None:
            return None
        agent = await self.find_agent(caller)
        if agent is None:
            raise AgentNotApproved(
                "This agent could not be registered because too many are already waiting for "
                "approval. Ask an administrator to clear the list in the admin console."
            )
        state = agent.state()
        if state == "approved":
            await self._touch(caller)
            return agent
        raise AgentNotApproved(_NOT_APPROVED[state])

    async def find_agent(self, caller: Caller) -> AgentRecord | None:
        """The caller's agent, registering it as pending if it has never been seen. None when
        it is unknown and can't be registered either (too many are already waiting)."""
        assert caller.client_id is not None
        agent = await self._store.get_agent(caller.client_id)
        if agent is None:
            await self.note_client(caller)
            agent = await self._store.get_agent(caller.client_id)
        return agent

    async def note_client(self, caller: Caller, reported_name: str | None = None) -> None:
        """Record that this AI client acted for this person. A client seen for the first time
        becomes a pending request in the admin console."""
        if caller.client_id is None:
            return
        try:
            await self._store.register_agent(
                caller.client_id,
                user_sub=caller.sub,
                user_name=caller.name,
                reported_name=clean_reported_name(reported_name),
            )
            self._last_touched[caller.client_id] = time.monotonic()
        except Exception:
            # Knowing an agent exists is not worth failing its request over, but a client that
            # can't be registered will be refused as unknown just below, so this is safe.
            logger.exception("could not register agent %r", caller.client_id)

    async def _touch(self, caller: Caller) -> None:
        assert caller.client_id is not None
        last = self._last_touched.get(caller.client_id)
        if last is None or time.monotonic() - last >= _TOUCH_EVERY_S:
            await self.note_client(caller)

    # --- tools and connections ----------------------------------------------------------------

    async def require_tool(self, caller: Caller, tool: str) -> AgentRecord | None:
        agent = await self.check_agent(caller)
        if agent is not None and tool not in agent.allowed_tools:
            raise ToolNotPermitted(f"This agent is not permitted to use the '{tool}' tool.")
        if tool not in await self._store.allowed_tools(caller.subjects()):
            raise ToolNotPermitted(f"You are not permitted to use the '{tool}' tool.")
        return agent

    async def list_connections(
        self, caller: Caller, agent: AgentRecord | None = None
    ) -> list[ConnectionRecord]:
        records = await self._store.list_connections(caller.subjects())
        if agent is None:
            return records
        return [r for r in records if _agent_may_use(agent, r)]

    async def open_connection(
        self, caller: Caller, tool: str, connection_name: str
    ) -> ConnectionAccess:
        """Check the tool and the connection, and load the caller's tables on it."""
        agent = await self.require_tool(caller, tool)
        record = await self._store.get_connection(connection_name, caller.subjects())
        if record is None or (agent is not None and not _agent_may_use(agent, record)):
            raise ConnectionNotFound(
                f"Connection '{connection_name}' was not found or is not available to you."
            )
        granted = await self._store.allowed_tables(record.id, caller.subjects())
        return ConnectionAccess(record=record, tables=frozenset(t.casefold() for t in granted))


_NOT_APPROVED = {
    "pending": (
        "This agent has not been approved yet, so it cannot use this server. An administrator "
        "has to approve it in the admin console. Tell the user this, and try again once they "
        "say it has been approved."
    ),
    "blocked": "An administrator has blocked this agent. It cannot use this server.",
    "expired": (
        "This agent's approval has expired. An administrator has to renew it in the admin "
        "console before it can use this server again."
    ),
}


def _agent_may_use(agent: AgentRecord, record: ConnectionRecord) -> bool:
    return agent.all_connections or record.id in agent.connection_ids


def clean_reported_name(name: str | None) -> str | None:
    """A name an agent gave itself, made safe to show: it is untrusted text, so control
    characters and line breaks go and it is cut short."""
    if not name:
        return None
    cleaned = " ".join(_NOT_PRINTABLE.sub(" ", name).split())[:_MAX_NAME_CHARS]
    return cleaned or None
