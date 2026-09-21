"""AI clients (agents) must be approved before they can do anything, and what they may do is
capped by what they were approved for as well as by the person they act for."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import ANALYST, ANALYST_VIA_AGENT

from mcp_sql_server.errors import (
    AgentNotApproved,
    ConnectionNotFound,
    ToolNotPermitted,
)
from mcp_sql_server.models import AgentRecord
from mcp_sql_server.services.permission_service import (
    SCHEMA_TOOLS,
    TOOL_NAMES,
    TOOL_PRESETS,
    clean_reported_name,
)

AGENT = ANALYST_VIA_AGENT.client_id
assert AGENT is not None


def approve(env, **changes) -> AgentRecord:
    fields = {
        "id": uuid4(),
        "client_id": AGENT,
        "status": "approved",
        "allowed_tools": sorted(TOOL_NAMES),
        **changes,
    }
    env.store.agents[AGENT] = AgentRecord(**fields)
    return env.store.agents[AGENT]


# --- an agent nobody has approved -------------------------------------------------------------


async def test_a_new_agent_is_registered_as_pending_and_refused(env):
    with pytest.raises(AgentNotApproved, match="has not been approved yet"):
        await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")

    assert env.store.agents[AGENT].status == "pending"
    assert not env.adapter.calls  # nothing reached the database


async def test_the_person_alone_is_not_enough(env):
    """The analyst role may do everything here, but this agent has not been approved."""
    assert await env.schema.list_tables(ANALYST, "shop")  # no agent: allowed, as for stdio
    with pytest.raises(AgentNotApproved):
        await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")


async def test_asking_again_does_not_create_a_second_request(env):
    for _ in range(3):
        with pytest.raises(AgentNotApproved):
            await env.query.run_query(ANALYST_VIA_AGENT, "shop", "SELECT 1")
    assert len(env.store.agents) == 1


async def test_the_refusal_is_audited_with_the_agent_that_made_it(env):
    with pytest.raises(AgentNotApproved):
        await env.schema.list_connections(ANALYST_VIA_AGENT)
    entry = env.store.audit[-1]
    assert (entry.success, entry.client_id) == (False, AGENT)
    assert "not been approved" in (entry.error_message or "")


async def test_a_blocked_agent_is_refused_with_a_different_message(env):
    approve(env, status="blocked")
    with pytest.raises(AgentNotApproved, match="blocked this agent"):
        await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")


async def test_an_approval_that_has_run_out_is_refused_until_renewed(env):
    approve(env, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(AgentNotApproved, match="expired"):
        await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")

    approve(env, expires_at=datetime.now(UTC) + timedelta(days=1))
    assert await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")


async def test_when_too_many_agents_are_waiting_a_new_one_is_told_so(env):
    env.store.max_pending = 0
    with pytest.raises(AgentNotApproved, match="too many"):
        await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")
    assert AGENT not in env.store.agents


# --- an approved agent ---------------------------------------------------------------------------


async def test_an_approved_agent_can_do_what_its_person_can(env):
    approve(env)
    tables = await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")
    assert {t.name for t in tables} == {"customers", "orders", "categories"}
    result = await env.query.run_query(ANALYST_VIA_AGENT, "shop", "SELECT id FROM orders")
    assert result.row_count == 2


async def test_the_agents_tool_ceiling_applies_even_though_the_person_may_use_the_tool(env):
    approve(env, allowed_tools=list(SCHEMA_TOOLS))
    assert await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")
    with pytest.raises(ToolNotPermitted, match="This agent is not permitted"):
        await env.query.run_query(ANALYST_VIA_AGENT, "shop", "SELECT 1")
    with pytest.raises(ToolNotPermitted):
        await env.query.get_sample_rows(ANALYST_VIA_AGENT, "shop", "orders")
    assert not env.adapter.reached_database()


async def test_an_agent_cannot_use_a_tool_its_person_lacks(env):
    """The ceiling never adds anything: the person's own grants still decide."""
    env.store.tool_grants.discard(("role", "analyst", "run_query"))
    approve(env)  # the agent was approved for every tool
    with pytest.raises(ToolNotPermitted, match="You are not permitted"):
        await env.query.run_query(ANALYST_VIA_AGENT, "shop", "SELECT 1")


async def test_an_agent_limited_to_some_connections_cannot_see_the_others(env):
    approve(env, all_connections=False, connection_ids=[uuid4()])
    assert await env.schema.list_connections(ANALYST_VIA_AGENT) == []
    with pytest.raises(ConnectionNotFound):
        await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")


async def test_an_agent_limited_to_a_connection_can_use_it(env):
    approve(env, all_connections=False, connection_ids=[env.connection_id])
    assert [c.name for c in await env.schema.list_connections(ANALYST_VIA_AGENT)] == ["shop"]
    assert await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")


async def test_the_table_grants_of_the_person_still_apply_to_the_agent(env):
    approve(env)
    from mcp_sql_server.errors import TableNotFound

    with pytest.raises(TableNotFound):
        await env.schema.describe_table(ANALYST_VIA_AGENT, "shop", "payments")


async def test_seeing_an_approved_agent_again_is_only_written_now_and_then(env):
    approve(env)
    for _ in range(5):
        await env.schema.list_tables(ANALYST_VIA_AGENT, "shop")
    assert len(env.store.agent_sightings) == 1  # the first call; the next four were cached


# --- the trusted local transport ------------------------------------------------------------------


async def test_a_caller_with_no_client_is_the_local_stdio_transport(env):
    """No OAuth client means no agent to approve: stdio is trusted by whoever configured it."""
    assert ANALYST.client_id is None
    assert await env.schema.list_tables(ANALYST, "shop")
    assert env.store.agents == {}


# --- names an agent gives itself ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "shown"),
    [
        ("ChatApp 1.2", "ChatApp 1.2"),
        ("  spaced \t out \n name ", "spaced out name"),
        ("evil\x00\x1b[31mname", "evil [31mname"),
        ("x" * 500, "x" * 100),
        ("", None),
        (None, None),
        (" \n\t ", None),
    ],
)
def test_a_name_the_agent_gave_itself_is_made_safe_to_show(given, shown):
    assert clean_reported_name(given) == shown


def test_the_presets_are_subsets_of_the_real_tools():
    assert set(TOOL_PRESETS["explorer"]) < TOOL_NAMES
    assert "run_query" not in TOOL_PRESETS["explorer"]
    assert set(TOOL_PRESETS["analyst"]) == TOOL_NAMES
