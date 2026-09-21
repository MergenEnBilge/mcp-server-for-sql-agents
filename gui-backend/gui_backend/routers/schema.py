"""The schema metadata editor: the human-written descriptions the AI reads.

The table and column structure comes from the target database itself and can't be edited here.
What administrators control is the curated layer on top: a sentence or two per table and column
saying what the data *means*. Those go into `schema_descriptions`, which `describe_table` and
`search_schema` serve to the model.

Because descriptions are handed to an AI, saving one that reads like an instruction to it (for
example "ignore your previous instructions") is allowed but comes back with a warning: the MCP
server withholds such text, so the editor says so rather than letting the admin assume it works.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from gui_backend.auth import Admin, ContextDep
from gui_backend.changes import announce, log_change
from gui_backend.routers.permissions import load_record
from mcp_sql_server.errors import McpSqlError, TableNotFound
from mcp_sql_server.models import SecurityFlag
from mcp_sql_server.services.sanitizer import OutputSanitizer

router = APIRouter(prefix="/api/schema", tags=["schema"])

MAX_DESCRIPTION_CHARS = 2000
_sanitizer = OutputSanitizer()


class TableRow(BaseModel):
    name: str
    kind: str
    description: str
    described_columns: int
    # True when a description exists for a table the database no longer has.
    missing: bool = False


class TableList(BaseModel):
    connection_id: UUID
    connection_name: str
    reachable: bool
    error: str | None
    tables: list[TableRow]


class ColumnRow(BaseModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool
    db_comment: str | None  # the database's own comment, read-only here
    description: str
    updated_at: datetime | None
    updated_by: str | None
    withheld_rule: str | None


class ForeignKeyRow(BaseModel):
    columns: list[str]
    to_table: str
    to_columns: list[str]


class TableDetailOut(BaseModel):
    connection_id: UUID
    name: str
    kind: str
    db_comment: str | None
    description: str
    updated_at: datetime | None
    updated_by: str | None
    withheld_rule: str | None
    columns: list[ColumnRow]
    foreign_keys: list[ForeignKeyRow]


class DescriptionIn(BaseModel):
    connection_id: UUID
    table: Annotated[str, Field(min_length=1, max_length=300)]
    column: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(max_length=MAX_DESCRIPTION_CHARS)]


class DescriptionOut(BaseModel):
    table: str
    column: str | None
    description: str
    updated_at: datetime
    updated_by: str | None
    # Set when the text reads like an instruction to an AI: the MCP server won't pass it on.
    withheld_rule: str | None


def withheld_rule(description: str) -> str | None:
    flags: list[SecurityFlag] = []
    _sanitizer.clean_text(description, "description", flags)
    return flags[0].rule if flags else None


@router.get("/tables")
async def list_tables(connection_id: UUID, ctx: ContextDep, _admin: Admin) -> TableList:
    record = await load_record(ctx, connection_id)
    async with ctx.engine.connect() as db:
        described = {
            r.table_name: r
            for r in await db.execute(
                text(
                    "SELECT table_name, "
                    "max(description) FILTER (WHERE column_name IS NULL) AS description, "
                    "count(*) FILTER (WHERE column_name IS NOT NULL AND description <> '') AS columns "
                    "FROM schema_descriptions WHERE connection_id = :id GROUP BY table_name"
                ),
                {"id": connection_id},
            )
        }
    by_fold = {name.casefold(): row for name, row in described.items()}

    rows: list[TableRow] = []
    error: str | None = None
    try:
        adapter = await ctx.registry.adapter_for(record)
        live = await adapter.list_tables()
    except McpSqlError as exc:
        live, error = [], str(exc)
    except Exception as exc:  # the database is down, misconfigured, ...
        live, error = [], f"Could not read the table list: {type(exc).__name__}"

    seen: set[str] = set()
    for table in live:
        seen.add(table.name.casefold())
        row = by_fold.get(table.name.casefold())
        rows.append(
            TableRow(
                name=table.name,
                kind=table.kind,
                description=(row.description or "") if row else "",
                described_columns=row.columns if row else 0,
            )
        )
    if error is None:
        for name, row in described.items():
            if name.casefold() not in seen:
                rows.append(
                    TableRow(
                        name=name,
                        kind="table",
                        description=row.description or "",
                        described_columns=row.columns,
                        missing=True,
                    )
                )
    else:  # still show what has been written, so it can be reviewed
        rows = [
            TableRow(
                name=name,
                kind="table",
                description=row.description or "",
                described_columns=row.columns,
            )
            for name, row in described.items()
        ]
    rows.sort(key=lambda r: r.name.casefold())
    return TableList(
        connection_id=connection_id,
        connection_name=record.name,
        reachable=error is None,
        error=error,
        tables=rows,
    )


@router.get("/table")
async def get_table(
    connection_id: UUID,
    table: Annotated[str, Query(min_length=1)],
    ctx: ContextDep,
    _admin: Admin,
) -> TableDetailOut:
    record = await load_record(ctx, connection_id)
    try:
        adapter = await ctx.registry.adapter_for(record)
        described = await adapter.describe_table(table)
    except TableNotFound:
        raise HTTPException(404, f"'{table}' is not a table on {record.name}.") from None
    except McpSqlError as exc:
        raise HTTPException(502, str(exc)) from None

    async with ctx.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT column_name, description, updated_at, updated_by FROM schema_descriptions "
                    "WHERE connection_id = :id AND lower(table_name) = lower(:table)"
                ),
                {"id": connection_id, "table": described.name},
            )
        ).mappings()
        curated = {(r["column_name"] or "").casefold(): r for r in rows}

    def written(column: str | None) -> tuple[str, datetime | None, str | None, str | None]:
        row = curated.get((column or "").casefold())
        if row is None:
            return "", None, None, None
        return (
            row["description"],
            row["updated_at"],
            row["updated_by"],
            withheld_rule(row["description"]),
        )

    table_description, table_at, table_by, table_rule = written(None)
    columns = []
    for column in described.columns:
        description, at, by, rule = written(column.name)
        columns.append(
            ColumnRow(
                name=column.name,
                type=column.type,
                nullable=column.nullable,
                primary_key=column.primary_key,
                db_comment=column.comment,
                description=description,
                updated_at=at,
                updated_by=by,
                withheld_rule=rule,
            )
        )
    return TableDetailOut(
        connection_id=connection_id,
        name=described.name,
        kind=described.kind,
        db_comment=described.comment,
        description=table_description,
        updated_at=table_at,
        updated_by=table_by,
        withheld_rule=table_rule,
        columns=columns,
        foreign_keys=[
            ForeignKeyRow(
                columns=list(fk.columns), to_table=fk.to_table, to_columns=list(fk.to_columns)
            )
            for fk in described.foreign_keys
        ],
    )


@router.put("/description")
async def set_description(body: DescriptionIn, ctx: ContextDep, admin: Admin) -> DescriptionOut:
    """Write (or replace) the description of a table, or of one of its columns.

    The table and column must exist in the target database, so descriptions can't pile up for
    names that were never real. Writing an empty description clears it.
    """
    record = await load_record(ctx, body.connection_id)
    try:
        adapter = await ctx.registry.adapter_for(record)
        described = await adapter.describe_table(body.table)
    except TableNotFound:
        raise HTTPException(404, f"'{body.table}' is not a table on {record.name}.") from None
    except McpSqlError as exc:
        raise HTTPException(502, str(exc)) from None

    column_name: str | None = None
    if body.column is not None:
        match = next(
            (c for c in described.columns if c.name.casefold() == body.column.casefold()), None
        )
        if match is None:
            raise HTTPException(404, f"'{described.name}' has no column '{body.column}'.")
        column_name = match.name

    description = body.description.strip()
    async with ctx.engine.begin() as db:
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO schema_descriptions "
                        "(connection_id, table_name, column_name, description, updated_by) "
                        "VALUES (:c, :table, :column, :description, :by) "
                        "ON CONFLICT (connection_id, table_name, column_name) DO UPDATE "
                        "SET description = EXCLUDED.description, updated_by = EXCLUDED.updated_by "
                        "RETURNING description, updated_at, updated_by"
                    ),
                    {
                        "c": body.connection_id,
                        "table": described.name,
                        "column": column_name,
                        "description": description,
                        "by": admin.name or admin.sub,
                    },
                )
            )
            .mappings()
            .one()
        )
        target = f"{record.name}.{described.name}" + (f".{column_name}" if column_name else "")
        await log_change(
            db,
            admin,
            "schema.describe",
            "description",
            target,
            {"description": description[:500] if description else None, "cleared": not description},
        )
    await announce(ctx.cache)  # descriptions are cached alongside permissions
    return DescriptionOut(
        table=described.name,
        column=column_name,
        description=row["description"],
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
        withheld_rule=withheld_rule(description),
    )


@router.delete("/description", status_code=204)
async def delete_descriptions(
    connection_id: UUID,
    table: Annotated[str, Query(min_length=1)],
    ctx: ContextDep,
    admin: Admin,
) -> None:
    """Remove every description written for a table, its columns included. This is how
    descriptions left behind by a table that no longer exists are cleaned up."""
    record = await load_record(ctx, connection_id)
    async with ctx.engine.begin() as db:
        removed = (
            await db.execute(
                text(
                    "DELETE FROM schema_descriptions "
                    "WHERE connection_id = :id AND lower(table_name) = lower(:table)"
                ),
                {"id": connection_id, "table": table},
            )
        ).rowcount
        if not removed:
            raise HTTPException(404, f"No descriptions are stored for '{table}'.")
        await log_change(
            db,
            admin,
            "schema.clear",
            "description",
            f"{record.name}.{table}",
            {"removed": removed},
        )
    await announce(ctx.cache)
