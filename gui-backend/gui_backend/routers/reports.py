"""Saved reports: a named query on a connection, plus how to chart it.

This is a stub for now. The natural-language BI app that will create and run reports doesn't exist
yet, so this only gives it a table and an API to plug into. Two things are already enforced,
because they are cheap now and painful to retrofit: a report's SQL must be a read-only query the
MCP server would accept, and a connection that reports depend on can't be deleted out from under them.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from gui_backend.auth import Admin, ContextDep
from gui_backend.changes import log_change
from gui_backend.context import Context
from mcp_sql_server.errors import QueryRejected
from mcp_sql_server.services.query_validator import QueryValidator

router = APIRouter(prefix="/api/reports", tags=["reports"])

_validator = QueryValidator()


class ReportIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=2000)] = ""
    connection_id: UUID
    sql: Annotated[str, Field(min_length=1, max_length=20_000)]
    chart_config: dict[str, Any] = Field(default_factory=dict)


class ReportOut(BaseModel):
    id: UUID
    name: str
    description: str
    connection_id: UUID
    connection_name: str
    sql: str
    chart_config: dict[str, Any]
    owner_sub: str
    owner_name: str | None
    created_at: datetime
    updated_at: datetime


_SELECT = """
    SELECT r.id, r.name, r.description, r.connection_id, c.name AS connection_name, r.sql,
           r.chart_config, r.owner_sub, k.display_name AS owner_name, r.created_at, r.updated_at
    FROM saved_reports r
    JOIN connections c ON c.id = r.connection_id
    LEFT JOIN known_subjects k ON k.subject_type = 'user' AND k.subject_id = r.owner_sub
"""


async def _check_sql(ctx: Context, connection_id: UUID, sql: str) -> None:
    """Refuse SQL the MCP server wouldn't run, so a report can never hold a write."""
    async with ctx.engine.connect() as db:
        engine = (
            await db.execute(
                text("SELECT engine FROM connections WHERE id = :id"), {"id": connection_id}
            )
        ).scalar_one_or_none()
    if engine is None:
        raise HTTPException(422, "That connection doesn't exist.")
    try:
        _validator.validate(sql, dialect=engine, default_schema=None)
    except QueryRejected as exc:
        raise HTTPException(422, f"This isn't a read-only query: {exc}") from None


async def _fetch(ctx: Context, report_id: UUID) -> ReportOut:
    async with ctx.engine.connect() as db:
        row = (
            (await db.execute(text(_SELECT + " WHERE r.id = :id"), {"id": report_id}))
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(404, "That report doesn't exist.")
    return ReportOut(**row)


@router.get("")
async def list_reports(
    ctx: ContextDep, _admin: Admin, q: str | None = None, connection_id: UUID | None = None
) -> list[ReportOut]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if q:
        clauses.append("(r.name ILIKE :q OR r.description ILIKE :q)")
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if connection_id:
        clauses.append("r.connection_id = :connection_id")
        params["connection_id"] = connection_id
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    async with ctx.engine.connect() as db:
        rows = (
            await db.execute(text(_SELECT + where + " ORDER BY r.updated_at DESC"), params)
        ).mappings()
        return [ReportOut(**row) for row in rows]


@router.get("/{report_id}")
async def get_report(report_id: UUID, ctx: ContextDep, _admin: Admin) -> ReportOut:
    return await _fetch(ctx, report_id)


@router.post("", status_code=201)
async def create_report(body: ReportIn, ctx: ContextDep, admin: Admin) -> ReportOut:
    await _check_sql(ctx, body.connection_id, body.sql)
    async with ctx.engine.begin() as db:
        new_id = (
            await db.execute(
                text(
                    "INSERT INTO saved_reports (name, description, connection_id, sql, chart_config, owner_sub) "
                    "VALUES (:name, :description, :connection_id, :sql, :chart, :owner) RETURNING id"
                ).bindparams(bindparam("chart", type_=JSONB)),
                {
                    "name": body.name,
                    "description": body.description,
                    "connection_id": body.connection_id,
                    "sql": body.sql,
                    "chart": body.chart_config,
                    "owner": admin.sub,
                },
            )
        ).scalar_one()
        await log_change(db, admin, "report.create", "report", body.name, {"id": str(new_id)})
    return await _fetch(ctx, new_id)


@router.put("/{report_id}")
async def update_report(
    report_id: UUID, body: ReportIn, ctx: ContextDep, admin: Admin
) -> ReportOut:
    current = await _fetch(ctx, report_id)
    await _check_sql(ctx, body.connection_id, body.sql)
    changed = [
        field
        for field, before, after in (
            ("name", current.name, body.name),
            ("description", current.description, body.description),
            ("connection", current.connection_id, body.connection_id),
            ("sql", current.sql, body.sql),
            ("chart_config", current.chart_config, body.chart_config),
        )
        if before != after
    ]
    async with ctx.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE saved_reports SET name = :name, description = :description, "
                "connection_id = :connection_id, sql = :sql, chart_config = :chart WHERE id = :id"
            ).bindparams(bindparam("chart", type_=JSONB)),
            {
                "id": report_id,
                "name": body.name,
                "description": body.description,
                "connection_id": body.connection_id,
                "sql": body.sql,
                "chart": body.chart_config,
            },
        )
        if changed:
            await log_change(db, admin, "report.update", "report", body.name, {"changed": changed})
    return await _fetch(ctx, report_id)


@router.delete("/{report_id}", status_code=204)
async def delete_report(report_id: UUID, ctx: ContextDep, admin: Admin) -> None:
    current = await _fetch(ctx, report_id)
    async with ctx.engine.begin() as db:
        await db.execute(text("DELETE FROM saved_reports WHERE id = :id"), {"id": report_id})
        await log_change(db, admin, "report.delete", "report", current.name, {})
