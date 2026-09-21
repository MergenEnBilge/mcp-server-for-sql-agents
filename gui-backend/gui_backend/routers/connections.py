"""Register, edit, test and remove the target databases the MCP server may query.

Credentials are write-only. They are encrypted before they touch the database, are never
returned by any endpoint (responses only say whether a secret exists), and never appear in
the admin log. Changing a connection's name or engine isn't offered: other things refer to
the name, so create a new connection instead.
"""

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from gui_backend.auth import Admin, ContextDep
from gui_backend.changes import announce, log_change
from gui_backend.connection_check import SECRET_LOOKING, check_connection
from mcp_sql_server.services.connection_guard import check_host, resolve_sqlite_path
from mcp_sql_server.services.connection_registry import DRIVERS, driver_problem

router = APIRouter(prefix="/api", tags=["connections"])

Engine = Literal["postgresql", "mysql", "mssql", "sqlite"]
NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"

# Settings each engine takes (all non-secret). `required` ones must be present.
ENGINE_FIELDS: dict[str, dict[str, Any]] = {
    "postgresql": {
        "label": "PostgreSQL",
        "port": 5432,
        "required": ["host", "database", "username"],
    },
    "mysql": {
        "label": "MySQL / MariaDB",
        "port": 3306,
        "required": ["host", "database", "username"],
    },
    "mssql": {"label": "SQL Server", "port": 1433, "required": ["host", "database", "username"]},
    "sqlite": {"label": "SQLite (file)", "port": None, "required": ["path"]},
}
ALLOWED_DETAIL_KEYS = {"host", "port", "database", "username", "path", "options", "schemas"}
assert set(ENGINE_FIELDS) == set(DRIVERS), "every engine the server can open must be offered here"


class EngineInfo(BaseModel):
    engine: str
    label: str
    default_port: int | None
    required: list[str]
    available: bool  # False when this server has no driver for it
    unavailable_reason: str | None


class ConnectionIn(BaseModel):
    name: Annotated[str, Field(pattern=NAME_PATTERN)]
    engine: Engine
    description: Annotated[str, Field(max_length=500)] = ""
    details: dict[str, Any]
    secret: Annotated[str | None, Field(max_length=2000)] = None  # write-only
    is_active: bool = True


class ConnectionUpdate(BaseModel):
    description: Annotated[str, Field(max_length=500)] = ""
    details: dict[str, Any]
    secret: Annotated[str | None, Field(max_length=2000)] = None  # omit to keep the current one
    clear_secret: bool = False
    is_active: bool = True


class ConnectionTest(BaseModel):
    """Settings to try out before saving. If `connection_id` is given and no `secret`, the
    stored secret is used, so an existing connection can be re-tested after editing its host."""

    engine: Engine
    details: dict[str, Any]
    secret: str | None = None
    connection_id: UUID | None = None


class ConnectionOut(BaseModel):
    id: UUID
    name: str
    engine: str
    description: str
    details: dict[str, Any]
    has_secret: bool  # the secret itself is never returned
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime | None
    last_check_ok: bool | None
    last_check_error: str | None
    access_count: int


class TestOut(BaseModel):
    ok: bool
    message: str
    table_count: int | None = None
    latency_ms: int | None = None


class Subject(BaseModel):
    subject_type: Literal["user", "role"]
    subject_id: Annotated[str, Field(min_length=1, max_length=200)]
    display_name: str | None = None


class AccessList(BaseModel):
    subjects: list[Subject]


_SELECT = """
    SELECT c.id, c.name, c.engine, c.description, c.details,
           (c.secret_encrypted IS NOT NULL) AS has_secret, c.is_active, c.created_at,
           c.updated_at, c.last_checked_at, c.last_check_ok, c.last_check_error,
           (SELECT count(*) FROM connection_access a WHERE a.connection_id = c.id) AS access_count
    FROM connections c
"""


def validate_details(
    engine: str, details: dict[str, Any], sqlite_root: str | None = None
) -> dict[str, Any]:
    """Refuse anything that isn't a known non-secret setting for this engine, in plain words."""
    unknown = sorted(set(details) - ALLOWED_DETAIL_KEYS)
    if unknown:
        raise HTTPException(422, f"Unknown setting: {', '.join(unknown)}.")
    for key in details:
        if SECRET_LOOKING.search(key):
            raise HTTPException(
                422, f"'{key}' looks like a secret. Put credentials in the secret field."
            )
    options = details.get("options") or {}
    if not isinstance(options, dict) or not all(
        isinstance(v, str | int | bool) for v in options.values()
    ):
        raise HTTPException(422, "Options must be a set of simple name/value pairs.")
    for key, value in options.items():
        if SECRET_LOOKING.search(key) or SECRET_LOOKING.search(str(value)):
            raise HTTPException(
                422, f"Option '{key}' looks like a secret. Put credentials in the secret field."
            )
    schemas = details.get("schemas")
    if schemas is not None and not (
        isinstance(schemas, list) and all(isinstance(s, str) for s in schemas)
    ):
        raise HTTPException(422, "Schemas must be a list of names.")
    port = details.get("port")
    if port is not None and not (isinstance(port, int) and 0 < port < 65536):
        raise HTTPException(422, "The port must be a number between 1 and 65535.")

    missing = [f for f in ENGINE_FIELDS[engine]["required"] if not details.get(f)]
    if missing:
        raise HTTPException(422, f"Missing required setting: {', '.join(missing)}.")
    if engine != "sqlite" and re.search(r"[\s/@]", str(details["host"])):
        raise HTTPException(422, "The host should be a plain host name or address.")
    try:
        if engine == "sqlite":
            resolve_sqlite_path(str(details["path"]), sqlite_root)
        else:
            check_host(str(details["host"]))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {k: v for k, v in details.items() if v not in (None, "")}


async def _fetch(ctx: Any, connection_id: UUID) -> ConnectionOut:
    async with ctx.engine.connect() as db:
        row = (
            (await db.execute(text(_SELECT + " WHERE c.id = :id"), {"id": connection_id}))
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(404, "That connection doesn't exist.")
    return ConnectionOut(**row)


@router.get("/engines")
async def list_engines(_admin: Admin) -> list[EngineInfo]:
    problems = {engine: driver_problem(engine) for engine in ENGINE_FIELDS}
    return [
        EngineInfo(
            engine=e,
            label=i["label"],
            default_port=i["port"],
            required=i["required"],
            available=problems[e] is None,
            unavailable_reason=problems[e],
        )
        for e, i in ENGINE_FIELDS.items()
    ]


@router.get("/connections")
async def list_connections(ctx: ContextDep, _admin: Admin) -> list[ConnectionOut]:
    async with ctx.engine.connect() as db:
        rows = (await db.execute(text(_SELECT + " ORDER BY c.name"))).mappings()
        return [ConnectionOut(**row) for row in rows]


@router.get("/connections/{connection_id}")
async def get_connection(connection_id: UUID, ctx: ContextDep, _admin: Admin) -> ConnectionOut:
    return await _fetch(ctx, connection_id)


@router.post("/connections", status_code=201)
async def create_connection(body: ConnectionIn, ctx: ContextDep, admin: Admin) -> ConnectionOut:
    details = validate_details(body.engine, body.details, ctx.settings.sqlite_root)
    encrypted = ctx.secret_box.encrypt(body.secret) if body.secret else None
    try:
        async with ctx.engine.begin() as db:
            new_id = (
                await db.execute(
                    text(
                        "INSERT INTO connections "
                        "(name, engine, description, details, secret_encrypted, is_active, created_by) "
                        "VALUES (:name, :engine, :description, :details, :secret, :active, :by) "
                        "RETURNING id"
                    ).bindparams(bindparam("details", type_=JSONB)),
                    {
                        "name": body.name,
                        "engine": body.engine,
                        "description": body.description,
                        "details": details,
                        "secret": encrypted,
                        "active": body.is_active,
                        "by": admin.sub,
                    },
                )
            ).scalar_one()
            await log_change(
                db,
                admin,
                "connection.create",
                "connection",
                body.name,
                {"engine": body.engine, "details": details, "has_secret": encrypted is not None},
            )
    except IntegrityError:
        raise HTTPException(409, f"A connection named '{body.name}' already exists.") from None
    await announce(ctx.cache, schema=True)
    return await _fetch(ctx, new_id)


@router.put("/connections/{connection_id}")
async def update_connection(
    connection_id: UUID, body: ConnectionUpdate, ctx: ContextDep, admin: Admin
) -> ConnectionOut:
    current = await _fetch(ctx, connection_id)
    details = validate_details(current.engine, body.details, ctx.settings.sqlite_root)

    changed = [
        name
        for name, before, after in (
            ("description", current.description, body.description),
            ("details", current.details, details),
            ("is_active", current.is_active, body.is_active),
        )
        if before != after
    ]
    # NB: only `secret` is ever written to this column; a plain `secret_encrypted = secret_encrypted`
    # keeps the stored value untouched when the admin didn't type a new one.
    secret_sql = "secret_encrypted"
    params: dict[str, Any] = {}
    if body.secret:
        secret_sql, params["secret"] = ":secret", ctx.secret_box.encrypt(body.secret)
        changed.append("secret")
    elif body.clear_secret:
        secret_sql = "NULL"
        changed.append("secret (cleared)")

    async with ctx.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE connections SET description = :description, details = :details, "
                f"is_active = :active, secret_encrypted = {secret_sql} WHERE id = :id"
            ).bindparams(bindparam("details", type_=JSONB)),
            {
                "id": connection_id,
                "description": body.description,
                "details": details,
                "active": body.is_active,
                **params,
            },
        )
        if changed:
            # Names of what changed, plus the new non-secret settings. Never the secret.
            await log_change(
                db,
                admin,
                "connection.update",
                "connection",
                current.name,
                {"changed": changed, "details": details if "details" in changed else None},
            )
    await announce(ctx.cache, schema=True)
    return await _fetch(ctx, connection_id)


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: UUID, ctx: ContextDep, admin: Admin) -> None:
    current = await _fetch(ctx, connection_id)
    async with ctx.engine.begin() as db:
        reports = (
            await db.execute(
                text("SELECT count(*) FROM saved_reports WHERE connection_id = :id"),
                {"id": connection_id},
            )
        ).scalar_one()
        if reports:
            raise HTTPException(
                409,
                f"'{current.name}' is used by {reports} saved report{'s' if reports != 1 else ''}. "
                "Delete or move those first.",
            )
        await db.execute(text("DELETE FROM connections WHERE id = :id"), {"id": connection_id})
        await log_change(
            db,
            admin,
            "connection.delete",
            "connection",
            current.name,
            {"engine": current.engine, "access_grants_removed": current.access_count},
        )
    await announce(ctx.cache, schema=True)


@router.post("/connections/test")
async def test_settings(body: ConnectionTest, ctx: ContextDep, _admin: Admin) -> TestOut:
    """Try settings that aren't saved yet (or edited ones) without changing anything."""
    details = validate_details(body.engine, body.details, ctx.settings.sqlite_root)
    password = body.secret
    if password is None and body.connection_id is not None:
        async with ctx.engine.connect() as db:
            stored = (
                await db.execute(
                    text("SELECT secret_encrypted FROM connections WHERE id = :id"),
                    {"id": body.connection_id},
                )
            ).scalar_one_or_none()
        password = ctx.secret_box.decrypt(stored) if stored else None
    result = await check_connection(
        body.engine,
        details,
        password,
        timeout_s=ctx.settings.connection_test_timeout_s,
        sqlite_root=ctx.settings.sqlite_root,
    )
    return TestOut(**result.__dict__)


@router.post("/connections/{connection_id}/test")
async def test_saved_connection(connection_id: UUID, ctx: ContextDep, admin: Admin) -> TestOut:
    """Test a saved connection with its stored credentials and record the outcome."""
    current = await _fetch(ctx, connection_id)
    async with ctx.engine.connect() as db:
        stored = (
            await db.execute(
                text("SELECT secret_encrypted FROM connections WHERE id = :id"),
                {"id": connection_id},
            )
        ).scalar_one_or_none()
    password = ctx.secret_box.decrypt(stored) if stored else None
    result = await check_connection(
        current.engine,
        current.details,
        password,
        timeout_s=ctx.settings.connection_test_timeout_s,
        sqlite_root=ctx.settings.sqlite_root,
    )
    async with ctx.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE connections SET last_checked_at = now(), last_check_ok = :ok, "
                "last_check_error = :error WHERE id = :id"
            ),
            {"id": connection_id, "ok": result.ok, "error": None if result.ok else result.message},
        )
    return TestOut(**result.__dict__)


# --- who may use a connection -------------------------------------------------------------------


@router.get("/connections/{connection_id}/access")
async def get_access(connection_id: UUID, ctx: ContextDep, _admin: Admin) -> AccessList:
    await _fetch(ctx, connection_id)
    async with ctx.engine.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT a.subject_type, a.subject_id, k.display_name FROM connection_access a "
                    "LEFT JOIN known_subjects k USING (subject_type, subject_id) "
                    "WHERE a.connection_id = :id ORDER BY a.subject_type DESC, a.subject_id"
                ),
                {"id": connection_id},
            )
        ).mappings()
        return AccessList(subjects=[Subject(**r) for r in rows])


@router.put("/connections/{connection_id}/access")
async def set_access(
    connection_id: UUID, body: AccessList, ctx: ContextDep, admin: Admin
) -> AccessList:
    """Replace the whole list of users and roles who may use this connection."""
    current = await _fetch(ctx, connection_id)
    wanted = {(s.subject_type, s.subject_id) for s in body.subjects}
    async with ctx.engine.begin() as db:
        existing = {
            (r.subject_type, r.subject_id)
            for r in await db.execute(
                text(
                    "SELECT subject_type, subject_id FROM connection_access WHERE connection_id = :id"
                ),
                {"id": connection_id},
            )
        }
        for subject_type, subject_id in existing - wanted:
            await db.execute(
                text(
                    "DELETE FROM connection_access WHERE connection_id = :id "
                    "AND subject_type = :t AND subject_id = :s"
                ),
                {"id": connection_id, "t": subject_type, "s": subject_id},
            )
        for subject_type, subject_id in wanted - existing:
            await db.execute(
                text(
                    "INSERT INTO connection_access (connection_id, subject_type, subject_id, granted_by) "
                    "VALUES (:id, :t, :s, :by)"
                ),
                {"id": connection_id, "t": subject_type, "s": subject_id, "by": admin.sub},
            )
        if wanted != existing:
            await log_change(
                db,
                admin,
                "connection.access",
                "connection",
                current.name,
                {
                    "granted": sorted(f"{t}:{s}" for t, s in wanted - existing),
                    "revoked": sorted(f"{t}:{s}" for t, s in existing - wanted),
                },
            )
    await announce(ctx.cache)
    return await get_access(connection_id, ctx, admin)
