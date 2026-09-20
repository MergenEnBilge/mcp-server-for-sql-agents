"""Schema discovery: what databases, tables and columns exist, and how they relate.

Everything is scoped to what the caller is allowed to see. That includes the
places a hidden table could leak through: a foreign key pointing at a table the
caller can't see is left out, and search results only cover their own tables.

Descriptions come from two places. The curated ones an admin wrote in the GUI
(app_meta.schema_descriptions) win; the database's own comments are the fallback.
Both are untrusted text and both go through the sanitizer.
"""

from mcp_sql_server.errors import InvalidArgument
from mcp_sql_server.models import (
    Caller,
    ColumnDetail,
    ConnectionInfo,
    ForeignKeyDetail,
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
from mcp_sql_server.services.permission_service import PermissionService
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

    async def list_connections(self, caller: Caller) -> list[ConnectionInfo]:
        async with self._audit.record(caller, "list_connections", {}) as rec:
            await self._permissions.require_tool(caller, "list_connections")
            flags: list[SecurityFlag] = []
            clean = self._sanitizer.clean_text
            connections = [
                ConnectionInfo(
                    name=record.name,
                    engine=record.engine,
                    description=clean(record.description, f"description of {record.name}", flags),
                )
                for record in await self._permissions.list_connections(caller)
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


def _fk_detail(fk: Relationship) -> ForeignKeyDetail:
    return ForeignKeyDetail(
        from_table=fk.from_table,
        columns=list(fk.columns),
        to_table=fk.to_table,
        to_columns=list(fk.to_columns),
    )


def _summary(text: str, flags: list[SecurityFlag]) -> str:
    return f"{text}; {len(flags)} value(s) withheld by the sanitizer" if flags else text
