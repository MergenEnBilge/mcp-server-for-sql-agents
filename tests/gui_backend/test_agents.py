"""The admin API for AI clients: seeing who is waiting, approving, limiting, blocking, removing.
The MCP server's own services are wired to the same database and cache, so each test can also
check what an agent is really allowed to do afterwards."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from mcp_sql_server.config import Settings as McpSettings
from mcp_sql_server.container import build_services
from mcp_sql_server.errors import AgentNotApproved, ToolNotPermitted
from mcp_sql_server.models import Caller
from mcp_sql_server.services.permission_service import SCHEMA_TOOLS, TOOL_NAMES

PREFIX = "gui-test-"


@pytest.fixture
async def mcp(api, postgres, fernet_key):
    settings = McpSettings(
        _env_file=None,
        app_meta_url=postgres.url("mcp_app", "app_meta"),
        connection_secret_keys=fernet_key,
    )
    services = build_services(settings, cache=api.cache)
    yield services
    await services.close()


@pytest.fixture(autouse=True)
async def leave_no_agents_behind(api):
    yield
    async with api.owner.begin() as db:
        await db.execute(text("DELETE FROM agents WHERE client_id LIKE :p"), {"p": PREFIX + "%"})


def person(client_id: str) -> Caller:
    return Caller(sub="person-1", name="Pat", roles=frozenset({"analyst"}), client_id=client_id)


async def connect(api, mcp, client_id: str, reported_name: str | None = "TestChat 1.0") -> dict:
    """What happens when a new AI client first connects: the MCP server notes it as pending."""
    await mcp.permissions.note_client(person(client_id), reported_name)
    pending = (await api.client.get("/api/agents/pending", headers=api.admin)).json()
    return next(a for a in pending["agents"] if a["client_id"] == client_id)


def new_client() -> str:
    return f"{PREFIX}{uuid4().hex[:8]}"


async def approve(api, agent_id: str, **body):
    body = {"tools": list(TOOL_NAMES), **body}
    return await api.client.post(f"/api/agents/{agent_id}/approve", json=body, headers=api.admin)


async def connection_id(api, name="shop-pg") -> str:
    connections = (await api.client.get("/api/connections", headers=api.admin)).json()
    return next(c["id"] for c in connections if c["name"] == name)


async def log_actions(api, client_id: str) -> list[str]:
    async with api.owner.connect() as db:
        rows = await db.execute(
            text("SELECT action FROM admin_log WHERE target = :t ORDER BY id"), {"t": client_id}
        )
        return [r.action for r in rows]


# --- who may look ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/agents"),
        ("GET", "/api/agents/pending"),
        ("GET", "/api/agents/options"),
        ("POST", f"/api/agents/{uuid4()}/approve"),
        ("POST", f"/api/agents/{uuid4()}/block"),
        ("DELETE", f"/api/agents/{uuid4()}"),
        ("POST", "/api/agents"),
    ],
)
async def test_only_administrators_may_use_it(api, method, path):
    body = {"tools": ["list_tables"], "client_id": "x"} if method == "POST" else None
    anonymous = await api.client.request(method, path, json=body)
    assert anonymous.status_code == 401
    member = await api.client.request(method, path, json=body, headers=api.member)
    assert member.status_code == 403


# --- a new client shows up ----------------------------------------------------------------------------


async def test_a_new_client_is_listed_as_pending_with_what_is_known_about_it(api, mcp):
    client_id = new_client()
    agent = await connect(api, mcp, client_id)

    assert agent["state"] == "pending"
    assert agent["reported_name"] == "TestChat 1.0"
    assert agent["last_user_sub"] == "person-1" and agent["last_user_name"] == "Pat"
    assert agent["allowed_tools"] == [] and agent["calls_24h"] == 0
    pending = (await api.client.get("/api/agents/pending", headers=api.admin)).json()
    assert pending["count"] >= 1


async def test_pending_requests_come_first_in_the_full_list(api, mcp):
    first = await connect(api, mcp, new_client())
    other = await connect(api, mcp, new_client())
    await approve(api, other["id"])

    states = [a["state"] for a in (await api.client.get("/api/agents", headers=api.admin)).json()]
    assert states == sorted(states, key=lambda s: s != "pending")
    assert first["state"] == "pending"


async def test_the_approval_form_is_offered_the_tools_presets_and_databases(api):
    options = (await api.client.get("/api/agents/options", headers=api.admin)).json()
    assert {t["name"] for t in options["tools"]} == TOOL_NAMES
    assert all(t["description"] for t in options["tools"])
    assert options["presets"]["explorer"] == list(SCHEMA_TOOLS)
    assert set(options["presets"]["analyst"]) == TOOL_NAMES
    assert {"shop-pg", "shop-sqlite"} <= {c["name"] for c in options["connections"]}


# --- approving -------------------------------------------------------------------------------------------


async def test_approving_takes_effect_at_once_and_is_limited_to_what_was_chosen(api, mcp):
    client_id = new_client()
    agent = await connect(api, mcp, client_id)
    caller = person(client_id)

    with pytest.raises(AgentNotApproved):
        await mcp.schema.list_connections(caller)

    response = await approve(api, agent["id"], tools=list(SCHEMA_TOOLS), label="Pat's chatbot")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["label"], body["expires_at"]) == ("approved", "Pat's chatbot", None)
    assert body["decided_by"] == "admin-1"

    assert [c.name for c in await mcp.schema.list_connections(caller)]  # allowed now
    with pytest.raises(ToolNotPermitted, match="This agent is not permitted"):
        await mcp.query.run_query(caller, "shop-pg", "SELECT 1")  # but not this tool


async def test_an_approval_can_end_on_its_own(api, mcp):
    client_id = new_client()
    agent = await connect(api, mcp, client_id)
    body = (await approve(api, agent["id"], expires_in_hours=24)).json()
    assert body["expires_at"] is not None

    async with api.owner.begin() as db:
        await db.execute(
            text("UPDATE agents SET expires_at = now() - interval '1 minute' WHERE client_id = :c"),
            {"c": client_id},
        )
    await api.cache.bump("meta")
    listed = next(
        a
        for a in (await api.client.get("/api/agents", headers=api.admin)).json()
        if a["client_id"] == client_id
    )
    assert listed["state"] == "expired"
    with pytest.raises(AgentNotApproved, match="expired"):
        await mcp.schema.list_connections(person(client_id))

    renewed = (await approve(api, agent["id"], expires_in_hours=48)).json()
    assert renewed["state"] == "approved"
    assert await mcp.schema.list_connections(person(client_id))


async def test_an_agent_can_be_limited_to_some_databases(api, mcp):
    client_id = new_client()
    agent = await connect(api, mcp, client_id)
    sqlite = await connection_id(api, "shop-sqlite")
    body = (await approve(api, agent["id"], all_connections=False, connection_ids=[sqlite])).json()

    assert body["all_connections"] is False and body["connections"] == ["shop-sqlite"]
    names = [c.name for c in await mcp.schema.list_connections(person(client_id))]
    assert names == ["shop-sqlite"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"tools": []}, None),
        ({"tools": ["drop_everything"]}, "Unknown tool"),
        ({"all_connections": False, "connection_ids": []}, "at least one database"),
        ({"all_connections": False, "connection_ids": [str(uuid4())]}, "doesn't exist"),
        ({"expires_in_hours": 0}, None),
        ({"label": "x" * 101}, None),
    ],
)
async def test_a_nonsensical_choice_is_refused_and_changes_nothing(api, mcp, body, message):
    agent = await connect(api, mcp, new_client())
    response = await approve(api, agent["id"], **body)
    assert response.status_code == 422
    if message:
        assert message in response.text
    again = (await api.client.get("/api/agents/pending", headers=api.admin)).json()
    assert any(a["id"] == agent["id"] for a in again["agents"])  # still waiting


async def test_changing_an_approval_narrows_it_straight_away(api, mcp):
    client_id = new_client()
    agent = await connect(api, mcp, client_id)
    await approve(api, agent["id"])
    assert (await mcp.query.run_query(person(client_id), "shop-sqlite", "SELECT 1")).row_count == 1

    await approve(api, agent["id"], tools=list(SCHEMA_TOOLS))
    with pytest.raises(ToolNotPermitted):
        await mcp.query.run_query(person(client_id), "shop-sqlite", "SELECT 1")
    assert await log_actions(api, client_id) == ["agent.approve", "agent.update"]


# --- pre-approving -----------------------------------------------------------------------------------------


async def test_an_agent_can_be_approved_before_it_ever_connects(api, mcp):
    client_id = new_client()
    response = await api.client.post(
        "/api/agents",
        json={"client_id": client_id, "tools": ["list_connections"], "label": "Nightly job"},
        headers=api.admin,
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "approved"
    assert [c.name for c in await mcp.schema.list_connections(person(client_id))]
    assert await log_actions(api, client_id) == ["agent.pre_approve"]


async def test_the_same_client_id_cannot_be_added_twice(api, mcp):
    client_id = new_client()
    body = {"client_id": client_id, "tools": ["list_connections"]}
    assert (await api.client.post("/api/agents", json=body, headers=api.admin)).status_code == 201
    again = await api.client.post("/api/agents", json=body, headers=api.admin)
    assert again.status_code == 409 and "already exists" in again.json()["detail"]


@pytest.mark.parametrize("client_id", ["", "has space", "x" * 201])
async def test_a_client_id_must_be_a_single_reasonable_token(api, client_id):
    response = await api.client.post(
        "/api/agents", json={"client_id": client_id, "tools": ["list_tables"]}, headers=api.admin
    )
    assert response.status_code == 422


# --- blocking and removing -----------------------------------------------------------------------------------


async def test_blocking_stops_an_approved_agent_immediately(api, mcp):
    client_id = new_client()
    agent = await connect(api, mcp, client_id)
    await approve(api, agent["id"])
    assert await mcp.schema.list_connections(person(client_id))

    blocked = await api.client.post(f"/api/agents/{agent['id']}/block", headers=api.admin)
    assert blocked.json()["state"] == "blocked"
    with pytest.raises(AgentNotApproved, match="blocked"):
        await mcp.schema.list_connections(person(client_id))
    assert await log_actions(api, client_id) == ["agent.approve", "agent.block"]


async def test_a_blocked_agent_can_be_approved_again(api, mcp):
    agent = await connect(api, mcp, new_client())
    await api.client.post(f"/api/agents/{agent['id']}/block", headers=api.admin)
    assert (await approve(api, agent["id"])).json()["state"] == "approved"


async def test_removing_an_agent_makes_it_a_new_request_next_time(api, mcp):
    client_id = new_client()
    agent = await connect(api, mcp, client_id)
    await approve(api, agent["id"])

    assert (
        await api.client.delete(f"/api/agents/{agent['id']}", headers=api.admin)
    ).status_code == 204
    with pytest.raises(AgentNotApproved, match="not been approved"):
        await mcp.schema.list_connections(person(client_id))  # registered again, as pending
    assert (
        await api.client.delete(f"/api/agents/{agent['id']}", headers=api.admin)
    ).status_code == 404


async def test_unknown_agents_are_a_404(api):
    missing = uuid4()
    for method, path in (
        ("POST", f"/api/agents/{missing}/block"),
        ("POST", f"/api/agents/{missing}/approve"),
    ):
        response = await api.client.request(
            method, path, json={"tools": ["list_tables"]}, headers=api.admin
        )
        assert response.status_code == 404


# --- what the audit trail says --------------------------------------------------------------------------------


async def test_the_change_log_records_who_decided_and_what_but_nothing_secret(api, mcp):
    client_id = new_client()
    agent = await connect(api, mcp, client_id)
    await approve(api, agent["id"], tools=["list_tables"], expires_in_hours=24)

    async with api.owner.connect() as db:
        row = (
            await db.execute(
                text("SELECT actor_sub, target_type, details FROM admin_log WHERE target = :t"),
                {"t": client_id},
            )
        ).one()
    assert (row.actor_sub, row.target_type) == ("admin-1", "agent")
    assert row.details == {"tools": ["list_tables"], "connections": "all", "expires_in_hours": 24}
