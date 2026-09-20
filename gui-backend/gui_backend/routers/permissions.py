"""Who may see which tables, and who may call which tools.

Two grids over the same idea: rows are subjects (users and roles), columns are things they can
be granted, and a grant is a row in the database (no row, no access). Every change is written
together with its admin-log entry and then announced to the MCP server's caches, so it takes
effect on the next request rather than after a cache expires.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from gui_backend.auth import Admin, ContextDep
from gui_backend.changes import announce, log_change
from gui_backend.context import Context
from mcp_sql_server.errors import McpSqlError
from mcp_sql_server.models import ConnectionRecord
from mcp_sql_server.services.permission_service import TOOL_NAMES

router = APIRouter(prefix="/api/permissions", tags=["permissions"])

SubjectType = Literal["user", "role"]

# What each tool does, in the words an admin needs when deciding who gets it.
TOOL_INFO = {
    "list_connections": "See which databases exist for them",
    "list_tables": "List the tables on a database",
    "describe_table": "See a table's columns and keys",
    "search_schema": "Search table and column names and descriptions",
    "get_relationships": "See how tables link to each other",
    "run_query": "Run read-only SELECT queries and see the results",
    "explain_query": "See how the database would run a query",
    "get_sample_rows": "See example rows from a table",
}
assert set(TOOL_INFO) == set(TOOL_NAMES), "describe every tool the MCP server offers"
TOOL_ORDER = list(TOOL_INFO)


class SubjectOut(BaseModel):
    subject_type: SubjectType
    subject_id: str
    display_name: str | None
    last_seen_at: datetime | None


class NewSubject(BaseModel):
    subject_type: SubjectType
    subject_id: Annotated[str, Field(min_length=1, max_length=200)]
    display_name: Annotated[str, Field(max_length=200)] | None = None


class TableGrant(BaseModel):
    subject_type: SubjectType
    subject_id: str
    table: str


class TableInfoOut(BaseModel):
    name: str
    kind: str
    description: str


class TableGrid(BaseModel):
    connection_id: UUID
    connection_name: str
    reachable: bool
    error: str | None
    tables: list[TableInfoOut]
    subjects: list[SubjectOut]
    with_connection_access: list[tuple[SubjectType, str]]
    grants: list[TableGrant]


class TableChange(BaseModel):
    table: Annotated[str, Field(min_length=1, max_length=300)]
    granted: bool


class TableChanges(BaseModel):
    connection_id: UUID
    subject_type: SubjectType
    subject_id: Annotated[str, Field(min_length=1, max_length=200)]
    changes: Annotated[list[TableChange], Field(min_length=1, max_length=2000)]


class ToolInfoOut(BaseModel):
    name: str
    description: str


class ToolGrant(BaseModel):
    subject_type: SubjectType
    subject_id: str
    tool: str


class ToolGrid(BaseModel):
    tools: list[ToolInfoOut]
    subjects: list[SubjectOut]
    grants: list[ToolGrant]


class ToolChange(BaseModel):
    tool: str
    granted: bool


class ToolChanges(BaseModel):
    subject_type: SubjectType
    subject_id: Annotated[str, Field(min_length=1, max_length=200)]
    changes: Annotated[list[ToolChange], Field(min_length=1, max_length=50)]


_SUBJECTS = """
    SELECT subject_type, subject_id, max(display_name) AS display_name,
           max(last_seen_at) AS last_seen_at
    FROM (
        SELECT subject_type, subject_id, display_name, last_seen_at FROM known_subjects
        UNION ALL SELECT subject_type, subject_id, NULL, NULL FROM connection_access
        UNION ALL SELECT subject_type, subject_id, NULL, NULL FROM table_permissions
        UNION ALL SELECT subject_type, subject_id, NULL, NULL FROM tool_permissions
    ) s
    GROUP BY subject_type, subject_id
    ORDER BY subject_type DESC, lower(coalesce(max(display_name), subject_id))
"""


async def _subjects(db: AsyncConnection) -> list[SubjectOut]:
    return [SubjectOut(**r) for r in (await db.execute(text(_SUBJECTS))).mappings()]


async def _remember_subject(db: AsyncConnection, subject_type: str, subject_id: str) -> None:
    """Make sure a subject that has been granted something shows up in the grid later."""
    await db.execute(
        text(
            "INSERT INTO known_subjects (subject_type, subject_id) VALUES (:t, :s) "
            "ON CONFLICT DO NOTHING"
        ),
        {"t": subject_type, "s": subject_id},
    )


async def load_record(ctx: Context, connection_id: UUID) -> ConnectionRecord:
    """A connection including its encrypted secret. Server-side use only."""
    async with ctx.engine.connect() as db:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT id, name, engine, description, details, secret_encrypted, updated_at "
                        "FROM connections WHERE id = :id"
                    ),
                    {"id": connection_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(404, "That connection doesn't exist.")
    return ConnectionRecord(**row)


# --- subjects -------------------------------------------------------------------------------------


@router.get("/subjects")
async def list_subjects(ctx: ContextDep, _admin: Admin) -> list[SubjectOut]:
    """Every user and role that has been seen or granted something."""
    async with ctx.engine.connect() as db:
        return await _subjects(db)


@router.post("/subjects", status_code=201)
async def add_subject(body: NewSubject, ctx: ContextDep, admin: Admin) -> SubjectOut:
    """Add a role (or a user) by name, e.g. before anyone holding it has signed in."""
    async with ctx.engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO known_subjects (subject_type, subject_id, display_name) "
                "VALUES (:t, :s, :n) ON CONFLICT (subject_type, subject_id) DO UPDATE "
                "SET display_name = COALESCE(EXCLUDED.display_name, known_subjects.display_name)"
            ),
            {"t": body.subject_type, "s": body.subject_id, "n": body.display_name},
        )
        await log_change(
            db,
            admin,
            "subject.add",
            body.subject_type,
            body.subject_id,
            {"name": body.display_name},
        )
        found = next(
            s
            for s in await _subjects(db)
            if (s.subject_type, s.subject_id) == (body.subject_type, body.subject_id)
        )
    return found


# --- table access -----------------------------------------------------------------------------------


@router.get("/tables")
async def table_grid(connection_id: UUID, ctx: ContextDep, _admin: Admin) -> TableGrid:
    record = await load_record(ctx, connection_id)

    tables: list[TableInfoOut] = []
    error: str | None = None
    descriptions: dict[str, str] = {}
    async with ctx.engine.connect() as db:
        for r in await db.execute(
            text(
                "SELECT lower(table_name), description FROM schema_descriptions "
                "WHERE connection_id = :id AND column_name IS NULL AND description <> ''"
            ),
            {"id": connection_id},
        ):
            descriptions[r[0]] = r[1]
        grants = [
            TableGrant(**r)
            for r in (
                await db.execute(
                    text(
                        'SELECT subject_type, subject_id, table_name AS "table" '
                        "FROM table_permissions WHERE connection_id = :id ORDER BY 3, 1, 2"
                    ),
                    {"id": connection_id},
                )
            ).mappings()
        ]
        with_access = [
            (r.subject_type, r.subject_id)
            for r in await db.execute(
                text(
                    "SELECT subject_type, subject_id FROM connection_access WHERE connection_id = :id"
                ),
                {"id": connection_id},
            )
        ]
        subjects = await _subjects(db)

    try:
        adapter = await ctx.registry.adapter_for(record)
        tables = [
            TableInfoOut(
                name=t.name, kind=t.kind, description=descriptions.get(t.name.casefold(), "")
            )
            for t in await adapter.list_tables()
        ]
    except McpSqlError as exc:
        error = str(exc)
    except Exception as exc:  # the target database is down, misconfigured, ...
        error = f"Could not read the table list: {type(exc).__name__}"

    if error is not None:
        # Still show what has been granted, so access can be reviewed and revoked.
        names = sorted({g.table for g in grants})
        tables = [
            TableInfoOut(name=n, kind="table", description=descriptions.get(n.casefold(), ""))
            for n in names
        ]

    return TableGrid(
        connection_id=connection_id,
        connection_name=record.name,
        reachable=error is None,
        error=error,
        tables=tables,
        subjects=subjects,
        with_connection_access=with_access,
        grants=grants,
    )


@router.put("/tables")
async def change_table_access(
    body: TableChanges, ctx: ContextDep, admin: Admin
) -> list[TableGrant]:
    """Grant or revoke tables for one user or role. Returns their grants on this connection."""
    record = await load_record(ctx, body.connection_id)
    granted = sorted({c.table for c in body.changes if c.granted})
    revoked = sorted({c.table for c in body.changes if not c.granted} - set(granted))

    async with ctx.engine.begin() as db:
        await _remember_subject(db, body.subject_type, body.subject_id)
        key = {"c": body.connection_id, "t": body.subject_type, "s": body.subject_id}
        for table in granted:
            await db.execute(
                text(
                    "INSERT INTO table_permissions (connection_id, subject_type, subject_id, "
                    "table_name, granted_by) VALUES (:c, :t, :s, :table, :by) ON CONFLICT DO NOTHING"
                ),
                {**key, "table": table, "by": admin.sub},
            )
        for table in revoked:
            await db.execute(
                text(
                    "DELETE FROM table_permissions WHERE connection_id = :c AND subject_type = :t "
                    "AND subject_id = :s AND table_name = :table"
                ),
                {**key, "table": table},
            )
        await log_change(
            db,
            admin,
            "permission.tables",
            "connection",
            record.name,
            {
                "subject": f"{body.subject_type}:{body.subject_id}",
                "granted": granted,
                "revoked": revoked,
            },
        )
        now_granted = [
            TableGrant(**r)
            for r in (
                await db.execute(
                    text(
                        'SELECT subject_type, subject_id, table_name AS "table" FROM table_permissions '
                        "WHERE connection_id = :c AND subject_type = :t AND subject_id = :s ORDER BY 3"
                    ),
                    key,
                )
            ).mappings()
        ]
    await announce(ctx.cache)
    return now_granted


# --- tool access -------------------------------------------------------------------------------------


@router.get("/tools")
async def tool_grid(ctx: ContextDep, _admin: Admin) -> ToolGrid:
    async with ctx.engine.connect() as db:
        grants = [
            ToolGrant(**r)
            for r in (
                await db.execute(
                    text("SELECT subject_type, subject_id, tool_name AS tool FROM tool_permissions")
                )
            ).mappings()
        ]
        subjects = await _subjects(db)
    return ToolGrid(
        tools=[ToolInfoOut(name=n, description=TOOL_INFO[n]) for n in TOOL_ORDER],
        subjects=subjects,
        grants=grants,
    )


@router.put("/tools")
async def change_tool_access(body: ToolChanges, ctx: ContextDep, admin: Admin) -> list[ToolGrant]:
    unknown = sorted({c.tool for c in body.changes} - set(TOOL_NAMES))
    if unknown:
        raise HTTPException(422, f"Unknown tool: {', '.join(unknown)}.")
    granted = sorted({c.tool for c in body.changes if c.granted})
    revoked = sorted({c.tool for c in body.changes if not c.granted} - set(granted))

    async with ctx.engine.begin() as db:
        await _remember_subject(db, body.subject_type, body.subject_id)
        key = {"t": body.subject_type, "s": body.subject_id}
        for tool in granted:
            await db.execute(
                text(
                    "INSERT INTO tool_permissions (subject_type, subject_id, tool_name, granted_by) "
                    "VALUES (:t, :s, :tool, :by) ON CONFLICT DO NOTHING"
                ),
                {**key, "tool": tool, "by": admin.sub},
            )
        for tool in revoked:
            await db.execute(
                text(
                    "DELETE FROM tool_permissions WHERE subject_type = :t AND subject_id = :s "
                    "AND tool_name = :tool"
                ),
                {**key, "tool": tool},
            )
        await log_change(
            db,
            admin,
            "permission.tools",
            body.subject_type,
            body.subject_id,
            {"granted": granted, "revoked": revoked},
        )
        now_granted = [
            ToolGrant(**r)
            for r in (
                await db.execute(
                    text(
                        "SELECT subject_type, subject_id, tool_name AS tool FROM tool_permissions "
                        "WHERE subject_type = :t AND subject_id = :s ORDER BY 3"
                    ),
                    key,
                )
            ).mappings()
        ]
    await announce(ctx.cache)
    return now_granted
