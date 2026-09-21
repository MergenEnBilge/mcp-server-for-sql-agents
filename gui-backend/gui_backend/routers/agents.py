"""AI clients (agents) that have connected to the MCP server, and what they may do.

A client that connects for the first time shows up here as `pending` and can do nothing. An
administrator approves it (choosing the tools and databases it may use, and for how long), blocks
it, or removes it. Only this API can approve: the MCP server's own database role can add a
pending row and refresh who and when, nothing more.

An approval is a ceiling, not a grant. What an agent can actually do is the overlap of that
ceiling and what the signed-in person is allowed, so it never exceeds either.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import ARRAY, Text, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from gui_backend.auth import Admin, ContextDep
from gui_backend.changes import announce, log_change
from gui_backend.routers.permissions import TOOL_INFO, TOOL_ORDER
from mcp_sql_server.services.permission_service import TOOL_NAMES, TOOL_PRESETS

router = APIRouter(prefix="/api/agents", tags=["agents"])

State = Literal["pending", "approved", "blocked", "expired"]
ClientId = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]


class AgentOut(BaseModel):
    id: UUID
    client_id: str
    label: str
    reported_name: str | None  # what the agent called itself; not verified
    state: State
    allowed_tools: list[str]
    all_connections: bool
    connections: list[str]
    expires_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_user_sub: str | None
    last_user_name: str | None
    decided_by: str | None
    decided_at: datetime | None
    calls_24h: int


class Access(BaseModel):
    """What an administrator allows an agent."""

    label: Annotated[str, Field(max_length=100)] = ""
    tools: Annotated[list[str], Field(min_length=1, max_length=len(TOOL_NAMES))]
    all_connections: bool = True
    connection_ids: Annotated[list[UUID], Field(max_length=500)] = []
    expires_in_hours: Annotated[int, Field(ge=1, le=24 * 365)] | None = None


class NewAgent(Access):
    client_id: ClientId


class PendingOut(BaseModel):
    count: int
    agents: list[AgentOut]


class OptionsOut(BaseModel):
    tools: list[dict[str, str]]
    presets: dict[str, list[str]]
    connections: list[dict[str, str]]


_SELECT = """
    SELECT a.id, a.client_id, a.label, a.reported_name,
           CASE WHEN a.status = 'approved' AND a.expires_at IS NOT NULL AND a.expires_at <= now()
                THEN 'expired' ELSE a.status END AS state,
           a.allowed_tools, a.all_connections,
           COALESCE((SELECT array_agg(c.name ORDER BY c.name) FROM agent_connections ac
                     JOIN connections c ON c.id = ac.connection_id WHERE ac.agent_id = a.id),
                    '{}') AS connections,
           a.expires_at, a.first_seen_at, a.last_seen_at, a.last_user_sub, a.last_user_name,
           a.decided_by, a.decided_at,
           (SELECT count(*) FROM audit_log l
            WHERE l.client_id = a.client_id AND l.occurred_at > now() - interval '24 hours'
           ) AS calls_24h
    FROM agents a
"""
# Requests waiting for a decision first, then whoever was seen most recently.
_ORDER = " ORDER BY CASE a.status WHEN 'pending' THEN 0 ELSE 1 END, a.last_seen_at DESC"


async def _fetch(db: AsyncConnection, agent_id: UUID) -> AgentOut:
    row = (
        (await db.execute(text(_SELECT + " WHERE a.id = :id"), {"id": agent_id})).mappings().first()
    )
    if row is None:
        raise HTTPException(404, "That agent doesn't exist (it may have been removed).")
    return AgentOut(**row)


@router.get("/options")
async def options(ctx: ContextDep, _admin: Admin) -> OptionsOut:
    """What the approval form offers: the tools, ready-made choices, and the databases."""
    async with ctx.engine.connect() as db:
        rows = await db.execute(
            text("SELECT id, name, engine FROM connections WHERE is_active ORDER BY name")
        )
        connections = [{"id": str(r.id), "name": r.name, "engine": r.engine} for r in rows]
    return OptionsOut(
        tools=[{"name": t, "description": TOOL_INFO[t]} for t in TOOL_ORDER],
        presets={name: list(tools) for name, tools in TOOL_PRESETS.items()},
        connections=connections,
    )


@router.get("")
async def list_agents(ctx: ContextDep, _admin: Admin) -> list[AgentOut]:
    async with ctx.engine.connect() as db:
        return [AgentOut(**r) for r in (await db.execute(text(_SELECT + _ORDER))).mappings()]


@router.get("/pending")
async def pending(ctx: ContextDep, _admin: Admin) -> PendingOut:
    """The requests waiting for a decision. The console polls this so a new one pops up."""
    async with ctx.engine.connect() as db:
        rows = (
            await db.execute(
                text(_SELECT + " WHERE a.status = 'pending' ORDER BY a.first_seen_at LIMIT 50")
            )
        ).mappings()
        agents = [AgentOut(**r) for r in rows]
    return PendingOut(count=len(agents), agents=agents)


async def _check_access(db: AsyncConnection, access: Access) -> list[str]:
    """Reject a choice that makes no sense, and return the names of the chosen databases."""
    unknown = sorted(set(access.tools) - TOOL_NAMES)
    if unknown:
        raise HTTPException(422, f"Unknown tool: {', '.join(unknown)}.")
    if access.all_connections:
        return []
    if not access.connection_ids:
        raise HTTPException(422, "Choose at least one database, or allow all of them.")
    rows = await db.execute(
        text("SELECT id, name FROM connections WHERE id = ANY(CAST(:ids AS uuid[]))").bindparams(
            bindparam("ids", type_=ARRAY(Text()))
        ),
        {"ids": [str(i) for i in access.connection_ids]},
    )
    found = {r.id: r.name for r in rows}
    if len(found) != len(set(access.connection_ids)):
        raise HTTPException(422, "One of the chosen databases doesn't exist.")
    return sorted(found.values())


async def _apply(db: AsyncConnection, agent_id: UUID, access: Access, admin_sub: str) -> None:
    await db.execute(
        text(
            "UPDATE agents SET status = 'approved', allowed_tools = :tools, "
            "all_connections = :all, label = :label, decided_by = :by, decided_at = now(), "
            "expires_at = CASE WHEN CAST(:hours AS int) IS NULL THEN NULL "
            "ELSE now() + make_interval(hours => CAST(:hours AS int)) END WHERE id = :id"
        ).bindparams(bindparam("tools", type_=ARRAY(Text()))),
        {
            "id": agent_id,
            "tools": sorted(access.tools),
            "all": access.all_connections,
            "label": access.label.strip(),
            "by": admin_sub,
            "hours": access.expires_in_hours,
        },
    )
    await db.execute(text("DELETE FROM agent_connections WHERE agent_id = :id"), {"id": agent_id})
    if not access.all_connections:
        for connection_id in set(access.connection_ids):
            await db.execute(
                text("INSERT INTO agent_connections (agent_id, connection_id) VALUES (:a, :c)"),
                {"a": agent_id, "c": connection_id},
            )


def _describe(access: Access, connection_names: list[str]) -> dict[str, Any]:
    return {
        "tools": sorted(access.tools),
        "connections": "all" if access.all_connections else connection_names,
        "expires_in_hours": access.expires_in_hours,
    }


@router.post("", status_code=201)
async def pre_approve(body: NewAgent, ctx: ContextDep, admin: Admin) -> AgentOut:
    """Approve an agent before it has connected, from the client id its sign-in will use."""
    try:
        async with ctx.engine.begin() as db:
            names = await _check_access(db, body)
            agent_id = (
                await db.execute(
                    text("INSERT INTO agents (client_id) VALUES (:c) RETURNING id"),
                    {"c": body.client_id},
                )
            ).scalar_one()
            await _apply(db, agent_id, body, admin.sub)
            await log_change(
                db, admin, "agent.pre_approve", "agent", body.client_id, _describe(body, names)
            )
    except IntegrityError:
        raise HTTPException(
            409, f"An agent with the client id '{body.client_id}' already exists."
        ) from None
    await announce(ctx.cache)
    async with ctx.engine.connect() as db:
        return await _fetch(db, agent_id)


@router.post("/{agent_id}/approve")
async def approve(agent_id: UUID, body: Access, ctx: ContextDep, admin: Admin) -> AgentOut:
    """Allow an agent, or change what an already approved one may do (or renew it)."""
    async with ctx.engine.begin() as db:
        before = await _fetch(db, agent_id)
        names = await _check_access(db, body)
        await _apply(db, agent_id, body, admin.sub)
        action = "agent.update" if before.state in ("approved", "expired") else "agent.approve"
        await log_change(db, admin, action, "agent", before.client_id, _describe(body, names))
    await announce(ctx.cache)
    async with ctx.engine.connect() as db:
        return await _fetch(db, agent_id)


@router.post("/{agent_id}/block")
async def block(agent_id: UUID, ctx: ContextDep, admin: Admin) -> AgentOut:
    async with ctx.engine.begin() as db:
        before = await _fetch(db, agent_id)
        await db.execute(
            text(
                "UPDATE agents SET status = 'blocked', decided_by = :by, decided_at = now() "
                "WHERE id = :id"
            ),
            {"id": agent_id, "by": admin.sub},
        )
        await log_change(db, admin, "agent.block", "agent", before.client_id, {"was": before.state})
    await announce(ctx.cache)
    async with ctx.engine.connect() as db:
        return await _fetch(db, agent_id)


@router.delete("/{agent_id}", status_code=204)
async def remove(agent_id: UUID, ctx: ContextDep, admin: Admin) -> None:
    """Forget an agent. If it connects again it is a new request, waiting for a decision."""
    async with ctx.engine.begin() as db:
        before = await _fetch(db, agent_id)
        await db.execute(text("DELETE FROM agents WHERE id = :id"), {"id": agent_id})
        await log_change(
            db, admin, "agent.remove", "agent", before.client_id, {"was": before.state}
        )
    await announce(ctx.cache)
