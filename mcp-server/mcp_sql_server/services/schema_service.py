"""Schema discovery: what databases, tables and columns exist, and how they relate.

Everything is scoped to what the caller is allowed to see. That includes the
places a hidden table could leak through: a foreign key pointing at a table the
caller can't see is left out, and search results only cover their own tables.

Descriptions come from two places. The curated ones an admin wrote in the GUI
(app_meta.schema_descriptions) win; the database's own comments are the fallback.
Both are untrusted text and both go through the sanitizer.
"""

from mcp_sql_server.docs import GUIDE_URI
from mcp_sql_server.errors import InvalidArgument
from mcp_sql_server.models import (
    AgentAccessInfo,
    Caller,
    ColumnDetail,
    ConnectionInfo,
    ForeignKeyDetail,
    MyAccess,
    Relationship,
    SchemaSearchHit,
    SecurityFlag,
    TableDetail,
    TableRelationships,
    TableSummary,
)
from mcp_sql_server.services.audit_service import AuditService
from mcp_sql_server.services.connection_registry import AdapterProvider
from mcp_sql_server.services.meta_store import MetaStore
from mcp_sql_server.services.permission_service import TOOL_NAMES, PermissionService
from mcp_sql_server.services.sanitizer import OutputSanitizer
from mcp_sql_server.services.tool_service import ToolService

_MAX_KEYWORD_CHARS = 100
_MAX_SEARCH_RESULTS = 50


class SchemaService(ToolService):
    def __init__(
        self,
        permissions: PermissionService,
        adapters: AdapterProvider,
        audit: AuditService,
        sanitizer: OutputSanitizer,
        store: MetaStore,
    ) -> None:
        super().__init__(permissions, adapters, audit, sanitizer)
        self._store = store

    async def my_access(self, caller: Caller) -> MyAccess:
        """What this caller may do right now. Available to everyone who is signed in, including an
        agent that hasn't been approved: it is how an agent finds out why it is being refused, and
        what to tell the person it works for. It only ever describes the caller's own access."""
        async with self._audit.record(caller, "get_my_access", {}) as rec:
            agent = await self._permissions.find_agent(caller) if caller.client_id else None
            state = agent.state() if agent else ("pending" if caller.client_id else "approved")
            info = (
                AgentAccessInfo(
                    client_id=agent.client_id,
                    label=agent.label,
                    state=state,
                    allowed_tools=sorted(agent.allowed_tools),
                    all_connections=agent.all_connections,
                    expires_at=agent.expires_at,
                )
                if agent
                else None
            )

            tools: list[str] = []
            connections: list[str] = []
            if state == "approved":
                granted = await self._store.allowed_tools(caller.subjects())
                ceiling = set(agent.allowed_tools) if agent else set(TOOL_NAMES)
                tools = sorted(granted & ceiling)
                records = await self._permissions.list_connections(caller, agent)
                connections = [r.name for r in records]

            status = _STATUS[state]
            rec.summary = f"{status}; {len(tools)} tools, {len(connections)} connections"
            return MyAccess(
                user=caller.name or caller.sub,
                roles=sorted(caller.roles),
                agent=info,
                status=status,
                tools=["get_my_access", *tools] if state == "approved" else [],
                connections=connections,
                message=_NEXT_STEP.get(status) or _ready_message(tools, connections),
                guide=GUIDE_URI,
            )

    async def list_connections(self, caller: Caller) -> list[ConnectionInfo]:
        async with self._audit.record(caller, "list_connections", {}) as rec:
            agent = await self._permissions.require_tool(caller, "list_connections")
            flags: list[SecurityFlag] = []
            clean = self._sanitizer.clean_text
            connections = [
                ConnectionInfo(
                    name=record.name,
                    engine=record.engine,
                    description=clean(record.description, f"description of {record.name}", flags),
                )
                for record in await self._permissions.list_connections(caller, agent)
            ]
            rec.summary = _summary(f"{len(connections)} connections", flags)
            return connections

    async def list_tables(self, caller: Caller, connection_name: str) -> list[TableSummary]:
        args = {"connection_name": connection_name}
        async with self._audit.record(caller, "list_tables", args, connection_name) as rec:
            access, adapter = await self._open(caller, "list_tables", connection_name)
            descriptions = await self._store.descriptions(access.record.id)
            flags: list[SecurityFlag] = []
            clean = self._sanitizer.clean_text

            tables = []
            for info in await adapter.list_tables():
                if not access.allows(info.name):
                    continue
                described = descriptions.get((info.name.casefold(), None)) or info.comment or ""
                tables.append(
                    TableSummary(
                        name=clean(info.name, "table name", flags),
                        kind=info.kind,
                        description=clean(described, f"description of {info.name}", flags),
                    )
                )
            rec.tables = [t.name for t in tables]
            rec.summary = _summary(f"{len(tables)} tables", flags)
            return tables

    async def describe_table(self, caller: Caller, connection_name: str, table: str) -> TableDetail:
        args = {"connection_name": connection_name, "table_name": table}
        async with self._audit.record(caller, "describe_table", args, connection_name) as rec:
            access, adapter = await self._open(caller, "describe_table", connection_name)
            access.require_table(table)  # checked before touching the database
            rec.tables = [table]

            description = await adapter.describe_table(table)
            curated = await self._store.descriptions(access.record.id)
            flags: list[SecurityFlag] = []
            clean = self._sanitizer.clean_text
            key = description.name.casefold()

            detail = TableDetail(
                name=clean(description.name, "table name", flags),
                kind=description.kind,
                description=clean(
                    curated.get((key, None)) or description.comment or "",
                    f"description of {description.name}",
                    flags,
                ),
                columns=[
                    ColumnDetail(
                        name=clean(col.name, f"column name in {description.name}", flags),
                        type=col.type,
                        nullable=col.nullable,
                        primary_key=col.primary_key,
                        description=clean(
                            curated.get((key, col.name.casefold())) or col.comment or "",
                            f"description of {description.name}.{col.name}",
                            flags,
                        ),
                    )
                    for col in description.columns
                ],
                # A foreign key to a table the caller can't see would reveal that it exists.
                foreign_keys=[
                    _fk_detail(fk) for fk in description.foreign_keys if access.allows(fk.to_table)
                ],
                security_flags=flags,
            )
            rec.summary = _summary(f"{len(detail.columns)} columns", flags)
            return detail

    async def get_relationships(
        self, caller: Caller, connection_name: str, table: str
    ) -> TableRelationships:
        args = {"connection_name": connection_name, "table_name": table}
        async with self._audit.record(caller, "get_relationships", args, connection_name) as rec:
            access, adapter = await self._open(caller, "get_relationships", connection_name)
            access.require_table(table)
            rec.tables = [table]

            target = await adapter.describe_table(table)
            references = [fk for fk in target.foreign_keys if access.allows(fk.to_table)]

            # Foreign keys *into* this table live on other tables, so look at each one the
            # caller can see. (Fine for typical schemas; the Redis cache for describe_table,
            # planned for a later step, is what will keep this cheap on very large ones.)
            referenced_by: list[Relationship] = []
            for other in await adapter.list_tables():
                if other.kind != "table" or not access.allows(other.name):
                    continue
                described = await adapter.describe_table(other.name)
                referenced_by.extend(
                    fk
                    for fk in described.foreign_keys
                    if fk.to_table.casefold() == target.name.casefold()
                )

            rec.summary = f"{len(references)} references, {len(referenced_by)} referenced by"
            return TableRelationships(
                table=target.name,
                references=[_fk_detail(fk) for fk in references],
                referenced_by=[_fk_detail(fk) for fk in referenced_by],
            )

    async def search_schema(
        self, caller: Caller, connection_name: str, keyword: str, limit: int = 20
    ) -> list[SchemaSearchHit]:
        args = {"connection_name": connection_name, "keyword": keyword}
        async with self._audit.record(caller, "search_schema", args, connection_name) as rec:
            keyword = keyword.strip()
            if not keyword or len(keyword) > _MAX_KEYWORD_CHARS:
                raise InvalidArgument(
                    f"keyword must be between 1 and {_MAX_KEYWORD_CHARS} characters."
                )
            if not 1 <= limit <= _MAX_SEARCH_RESULTS:
                raise InvalidArgument(f"limit must be between 1 and {_MAX_SEARCH_RESULTS}.")

            access, _adapter = await self._open(caller, "search_schema", connection_name)
            hits = await self._store.search_descriptions(
                access.record.id, keyword, sorted(access.tables), limit
            )

            flags: list[SecurityFlag] = []
            clean = self._sanitizer.clean_text
            results = [
                hit.model_copy(
                    update={
                        "table": clean(hit.table, "table name", flags),
                        "column": clean(hit.column, "column name", flags) if hit.column else None,
                        "description": clean(hit.description, f"description of {hit.table}", flags),
                    }
                )
                for hit in hits
            ]
            rec.tables = sorted({hit.table for hit in results})
            rec.summary = _summary(f"{len(results)} matches", flags)
            return results


_STATUS = {
    "approved": "ready",
    "pending": "pending_approval",
    "blocked": "blocked",
    "expired": "expired",
}

_NEXT_STEP = {
    "pending_approval": (
        "This agent is waiting for an administrator to approve it in the admin console. Tell "
        "the user, and call get_my_access again to check."
    ),
    "blocked": (
        "An administrator has blocked this agent. Tell the user; nothing you do will change that."
    ),
    "expired": (
        "This agent's approval has expired. Tell the user an administrator has to renew it."
    ),
}


def _ready_message(tools: list[str], connections: list[str]) -> str:
    if not tools:
        return (
            "This agent is approved, but the person you work for hasn't been granted any tools. "
            "Tell them to ask an administrator."
        )
    if not connections:
        return (
            "You have tools, but no database you may use. Tell the person you work for to ask "
            "an administrator for access to one."
        )
    return (
        f"You can use {len(tools)} tools on {len(connections)} database(s). "
        f"Read {GUIDE_URI} to get started."
    )


def _fk_detail(fk: Relationship) -> ForeignKeyDetail:
    return ForeignKeyDetail(
        from_table=fk.from_table,
        columns=list(fk.columns),
        to_table=fk.to_table,
        to_columns=list(fk.to_columns),
    )


def _summary(text: str, flags: list[SecurityFlag]) -> str:
    return f"{text}; {len(flags)} value(s) withheld by the sanitizer" if flags else text
