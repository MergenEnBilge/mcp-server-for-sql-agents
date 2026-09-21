"""The first-connection flow, end to end over HTTP against the real database: a client nobody has
approved shows up as a pending request, is refused, and starts working the moment an
administrator approves it (with the limits they chose)."""

import uuid

import pytest
from fake_idp import FakeIdP
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from test_http_oauth import mcp_client, serve

REGISTERED = pytest.mark.usefixtures("registered")

SCHEMA_ONLY = ["list_connections", "list_tables", "describe_table"]


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
async def admin(postgres):
    """The admin API's database role: the only one allowed to approve an agent."""
    engine = create_async_engine(postgres.url("gui_app", "app_meta"))
    yield engine
    await engine.dispose()


def new_client_id() -> str:
    return f"chat-{uuid.uuid4().hex[:10]}"


def token_for(idp: FakeIdP, client_id: str, **kwargs) -> str:
    return idp.token(extra={"azp": client_id}, **kwargs)


async def agent_row(admin, client_id: str):
    async with admin.connect() as db:
        result = await db.execute(
            text("SELECT status, reported_name, last_user_sub FROM agents WHERE client_id = :c"),
            {"c": client_id},
        )
        return result.first()


async def approve(admin, client_id: str, tools: list[str], **columns) -> None:
    sets = ", ".join(f"{name} = :{name}" for name in columns)
    async with admin.begin() as db:
        await db.execute(
            text(
                "UPDATE agents SET status = 'approved', allowed_tools = :tools"
                + (f", {sets}" if sets else "")
                + " WHERE client_id = :c"
            ),
            {"c": client_id, "tools": tools, **columns},
        )


@REGISTERED
async def test_a_new_client_appears_as_pending_when_it_connects_and_is_refused(
    postgres, fernet_key, idp, admin
):
    client_id = new_client_id()
    with serve(postgres, fernet_key, idp) as server:
        async with mcp_client(server, token_for(idp, client_id, sub="person-1")) as client:
            # Saying hello is enough to show up: no tool call needed.
            row = await agent_row(admin, client_id)
            assert row is not None
            assert (row.status, row.last_user_sub) == ("pending", "person-1")
            assert row.reported_name  # what the client called itself

            # Discovery is open, but anything that touches data is not.
            assert (await client.list_tools()).tools
            refused = await client.call_tool("list_connections", {})
            assert refused.is_error
            assert "has not been approved yet" in refused.content[0].text


@REGISTERED
async def test_approving_it_lets_the_next_call_through_with_the_chosen_limits(
    postgres, fernet_key, idp, admin
):
    client_id = new_client_id()
    with serve(postgres, fernet_key, idp) as server:
        async with mcp_client(server, token_for(idp, client_id)) as client:
            assert (await client.call_tool("list_connections", {})).is_error
            await approve(admin, client_id, SCHEMA_ONLY)

            allowed = await client.call_tool("list_connections", {})
            assert not allowed.is_error
            tables = await client.call_tool("list_tables", {"connection_name": "shop-sqlite"})
            assert not tables.is_error

            # The person could run queries, but this agent wasn't approved for that.
            query = await client.call_tool(
                "run_query", {"connection_name": "shop-sqlite", "sql": "SELECT 1"}
            )
            assert query.is_error
            assert "This agent is not permitted" in query.content[0].text


@REGISTERED
async def test_blocking_an_agent_stops_it_at_once(postgres, fernet_key, idp, admin):
    client_id = new_client_id()
    with serve(postgres, fernet_key, idp) as server:
        async with mcp_client(server, token_for(idp, client_id)) as client:
            assert (await client.call_tool("list_connections", {})).is_error
            await approve(admin, client_id, SCHEMA_ONLY)
            assert not (await client.call_tool("list_connections", {})).is_error

            async with admin.begin() as db:
                await db.execute(
                    text("UPDATE agents SET status = 'blocked' WHERE client_id = :c"),
                    {"c": client_id},
                )
            blocked = await client.call_tool("list_connections", {})
            assert blocked.is_error
            assert "blocked this agent" in blocked.content[0].text


@REGISTERED
async def test_an_agent_limited_to_one_database_only_sees_that_one(
    postgres, fernet_key, idp, admin
):
    client_id = new_client_id()
    with serve(postgres, fernet_key, idp) as server:
        async with mcp_client(server, token_for(idp, client_id)) as client:
            assert (await client.call_tool("list_connections", {})).is_error
            await approve(admin, client_id, SCHEMA_ONLY, all_connections=False)
            async with admin.begin() as db:
                await db.execute(
                    text(
                        "INSERT INTO agent_connections (agent_id, connection_id) "
                        "SELECT a.id, c.id FROM agents a, connections c "
                        "WHERE a.client_id = :a AND c.name = 'shop-sqlite'"
                    ),
                    {"a": client_id},
                )
            listed = await client.call_tool("list_connections", {})
            assert [c["name"] for c in listed.structured_content["result"]] == ["shop-sqlite"]
            other = await client.call_tool("list_tables", {"connection_name": "shop-pg"})
            assert other.is_error
            assert "not found or is not available" in other.content[0].text


@REGISTERED
async def test_two_clients_of_the_same_person_are_approved_separately(
    postgres, fernet_key, idp, admin
):
    first, second = new_client_id(), new_client_id()
    with serve(postgres, fernet_key, idp) as server:
        async with mcp_client(server, token_for(idp, first, sub="same-person")) as a:
            assert (await a.call_tool("list_connections", {})).is_error
        await approve(admin, first, SCHEMA_ONLY)
        async with mcp_client(server, token_for(idp, first, sub="same-person")) as a:
            assert not (await a.call_tool("list_connections", {})).is_error
        async with mcp_client(server, token_for(idp, second, sub="same-person")) as b:
            assert (await b.call_tool("list_connections", {})).is_error


@REGISTERED
async def test_a_token_with_no_client_id_is_refused_outright(postgres, fernet_key, idp):
    with serve(postgres, fernet_key, idp) as server:
        token = idp.token(omit=("azp",))
        async with mcp_client(server, token) as client:
            result = await client.call_tool("list_connections", {})
            assert result.is_error
            assert "Authentication is required" in result.content[0].text or (
                "which application" in result.content[0].text
            )


@REGISTERED
async def test_calls_are_recorded_against_the_agent_that_made_them(
    postgres, fernet_key, idp, admin
):
    client_id = new_client_id()
    with serve(postgres, fernet_key, idp) as server:
        async with mcp_client(server, token_for(idp, client_id)) as client:
            await client.call_tool("list_connections", {})
            await approve(admin, client_id, SCHEMA_ONLY)
            await client.call_tool("list_connections", {})
    async with admin.connect() as db:
        rows = await db.execute(
            text("SELECT success FROM audit_log WHERE client_id = :c ORDER BY id"),
            {"c": client_id},
        )
        assert [r.success for r in rows] == [False, True]


@REGISTERED
async def test_the_mcp_servers_database_role_cannot_approve_anything(postgres):
    """Even a bug, or someone who took over the MCP server, can't grant an agent access: the
    role has no right to write the columns that decide it."""
    engine = create_async_engine(postgres.url("mcp_app", "app_meta"))
    client_id = new_client_id()
    try:
        async with engine.begin() as db:
            await db.execute(
                text("INSERT INTO agents (client_id, last_user_sub) VALUES (:c, 'x')"),
                {"c": client_id},
            )  # allowed: it can only ever create a pending row
        for statement in (
            "UPDATE agents SET status = 'approved' WHERE client_id = :c",
            "UPDATE agents SET allowed_tools = ARRAY['run_query'] WHERE client_id = :c",
            "UPDATE agents SET all_connections = true WHERE client_id = :c",
            "INSERT INTO agents (client_id, status) VALUES (:c2, 'approved')",
            (
                "INSERT INTO agent_connections (agent_id, connection_id) "
                "SELECT a.id, c.id FROM agents a, connections c WHERE a.client_id = :c LIMIT 1"
            ),
            "DELETE FROM agents WHERE client_id = :c",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with engine.begin() as db:
                    await db.execute(text(statement), {"c": client_id, "c2": new_client_id()})
        async with engine.connect() as db:
            status = (
                await db.execute(
                    text("SELECT status FROM agents WHERE client_id = :c"), {"c": client_id}
                )
            ).scalar_one()
        assert status == "pending"
    finally:
        await engine.dispose()
