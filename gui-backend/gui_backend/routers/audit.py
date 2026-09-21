"""Read-only views over the audit log (what AI callers did) and the admin log (what
administrators did). Both are append-only tables this service can only read."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from gui_backend.auth import Admin, ContextDep

router = APIRouter(prefix="/api", tags=["audit"])

# The only columns the list can be sorted by. Anything else is refused, so a sort value can
# never end up inside the SQL text.
SORTABLE = {
    "occurred_at": "occurred_at",
    "caller": "caller_sub",
    "tool": "tool_name",
    "connection": "connection_name",
    "duration": "duration_ms",
    "rows": "row_count",
    "status": "success",
}
SQL_PREVIEW_CHARS = 240


class AuditItem(BaseModel):
    id: int
    occurred_at: datetime
    caller_sub: str
    caller_name: str | None
    client_id: str | None  # the AI client that made the call; empty for the local stdio transport
    tool_name: str
    connection_name: str | None
    tables: list[str]
    sql_preview: str | None
    sql_truncated: bool
    success: bool
    error_preview: str | None
    row_count: int | None
    duration_ms: int


class AuditDetail(AuditItem):
    arguments: dict[str, Any]
    sql: str | None
    result_summary: str | None
    error_message: str | None


class AuditPage(BaseModel):
    items: list[AuditItem]
    total: int
    page: int
    page_size: int


class Person(BaseModel):
    sub: str
    name: str | None


class AuditFacets(BaseModel):
    """Values that appear in the recent log, for filter dropdowns."""

    callers: list[Person]
    tools: list[str]
    connections: list[str]
    tables: list[str]


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_LIST_COLUMNS = f"""
    id, occurred_at, caller_sub, caller_name, client_id, tool_name, connection_name, tables, success,
    row_count, duration_ms,
    left(arguments->>'sql', {SQL_PREVIEW_CHARS}) AS sql_preview,
    coalesce(length(arguments->>'sql'), 0) > {SQL_PREVIEW_CHARS} AS sql_truncated,
    left(error_message, 200) AS error_preview
"""


@router.get("/audit")
async def list_audit(
    ctx: ContextDep,
    _admin: Admin,
    user: Annotated[str | None, Query(description="Part of a name or user id")] = None,
    tool: str | None = None,
    connection: str | None = None,
    table: str | None = None,
    success: bool | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    q: Annotated[str | None, Query(description="Text in the arguments or error")] = None,
    sort: Literal["occurred_at", "caller", "tool", "connection", "duration", "rows", "status"] = (
        "occurred_at"
    ),
    order: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1)] = 50,
) -> AuditPage:
    page_size = min(page_size, ctx.settings.max_page_size)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if user:
        clauses.append("(caller_sub ILIKE :user OR caller_name ILIKE :user)")
        params["user"] = _like(user)
    if tool:
        clauses.append("tool_name = :tool")
        params["tool"] = tool
    if connection:
        clauses.append("connection_name = :connection")
        params["connection"] = connection
    if table:
        clauses.append("tables @> ARRAY[CAST(:table AS text)]")
        params["table"] = table
    if success is not None:
        clauses.append("success = :success")
        params["success"] = success
    if date_from:
        clauses.append("occurred_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append("occurred_at < :date_to")
        params["date_to"] = date_to
    if q:
        clauses.append("(arguments::text ILIKE :q OR error_message ILIKE :q)")
        params["q"] = _like(q)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    # `sort` and `order` are constrained to fixed values above, so building SQL from them is safe.
    ordering = f"{SORTABLE[sort]} {order.upper()} NULLS LAST, id DESC"
    async with ctx.engine.connect() as db:
        total = (
            await db.execute(text(f"SELECT count(*) FROM audit_log {where}"), params)
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    f"SELECT {_LIST_COLUMNS} FROM audit_log {where} "
                    f"ORDER BY {ordering} LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        ).mappings()
        items = [AuditItem(**row) for row in rows]
    return AuditPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/audit/facets")
async def audit_facets(ctx: ContextDep, _admin: Admin) -> AuditFacets:
    """What has appeared in the last 30 days, to fill the filter dropdowns."""
    recent = "WHERE occurred_at > now() - interval '30 days'"
    async with ctx.engine.connect() as db:
        callers = await db.execute(
            text(
                "SELECT caller_sub AS sub, max(caller_name) AS name FROM audit_log "
                f"{recent} GROUP BY caller_sub ORDER BY max(occurred_at) DESC LIMIT 500"
            )
        )
        tools = await db.execute(
            text(f"SELECT DISTINCT tool_name FROM audit_log {recent} ORDER BY 1")
        )
        connections = await db.execute(
            text(
                "SELECT DISTINCT connection_name FROM audit_log "
                f"{recent} AND connection_name IS NOT NULL ORDER BY 1"
            )
        )
        tables = await db.execute(
            text(
                f"SELECT DISTINCT unnest(tables) AS t FROM audit_log {recent} ORDER BY 1 LIMIT 1000"
            )
        )
        return AuditFacets(
            callers=[Person(sub=r.sub, name=r.name) for r in callers],
            tools=[r[0] for r in tools],
            connections=[r[0] for r in connections],
            tables=[r[0] for r in tables],
        )


@router.get("/audit/{entry_id}")
async def get_audit_entry(entry_id: int, ctx: ContextDep, _admin: Admin) -> AuditDetail:
    async with ctx.engine.connect() as db:
        row = (
            (
                await db.execute(
                    text(
                        f"SELECT {_LIST_COLUMNS}, arguments, result_summary, error_message, "
                        "arguments->>'sql' AS sql FROM audit_log WHERE id = :id"
                    ),
                    {"id": entry_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(404, detail="That audit entry doesn't exist.")
    return AuditDetail(**row)


# --- what administrators did -------------------------------------------------------------------


class AdminLogItem(BaseModel):
    id: int
    occurred_at: datetime
    actor_sub: str
    actor_name: str | None
    action: str
    target_type: str
    target: str | None
    details: dict[str, Any]


class AdminLogPage(BaseModel):
    items: list[AdminLogItem]
    total: int
    page: int
    page_size: int


@router.get("/admin-log")
async def list_admin_log(
    ctx: ContextDep,
    _admin: Admin,
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1)] = 50,
) -> AdminLogPage:
    page_size = min(page_size, ctx.settings.max_page_size)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if actor:
        clauses.append("(actor_sub ILIKE :actor OR actor_name ILIKE :actor)")
        params["actor"] = _like(actor)
    if action:
        clauses.append("action = :action")
        params["action"] = action
    if target_type:
        clauses.append("target_type = :target_type")
        params["target_type"] = target_type
    if date_from:
        clauses.append("occurred_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append("occurred_at < :date_to")
        params["date_to"] = date_to
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    async with ctx.engine.connect() as db:
        total = (
            await db.execute(text(f"SELECT count(*) FROM admin_log {where}"), params)
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    "SELECT id, occurred_at, actor_sub, actor_name, action, target_type, target, "
                    f"details FROM admin_log {where} ORDER BY occurred_at DESC, id DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        ).mappings()
        items = [AdminLogItem(**row) for row in rows]
    return AdminLogPage(items=items, total=total, page=page, page_size=page_size)
