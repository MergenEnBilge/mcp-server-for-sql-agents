"""The permission grids' API, and, crucially, that the MCP server honours changes at once."""

from uuid import uuid4

import pytest

from mcp_sql_server.cache.base import META
from mcp_sql_server.config import Settings as McpSettings
from mcp_sql_server.container import build_services
from mcp_sql_server.errors import TableNotFound, ToolNotPermitted
from mcp_sql_server.models import Caller


@pytest.fixture
async def mcp(api, postgres, fernet_key):
    """The MCP server's services, sharing the GUI's cache the way two processes share Redis."""
    settings = McpSettings(
        _env_file=None,
        app_meta_url=postgres.url("mcp_app", "app_meta"),
        connection_secret_keys=fernet_key,
    )
    services = build_services(settings, cache=api.cache)
    yield services
    await services.close()


async def connection_id(api, name="shop-pg") -> str:
    connections = (await api.client.get("/api/connections", headers=api.admin)).json()
    return next(c["id"] for c in connections if c["name"] == name)


async def change_tables(api, cid, subject_type, subject_id, **table_grants):
    response = await api.client.put(
        "/api/permissions/tables",
        json={
            "connection_id": cid,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "changes": [{"table": t, "granted": g} for t, g in table_grants.items()],
        },
        headers=api.admin,
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- subjects ---------------------------------------------------------------------------------------


async def test_a_role_can_be_added_before_anyone_holds_it(api):
    role = f"future-{uuid4().hex[:6]}"
    response = await api.client.post(
        "/api/permissions/subjects",
        json={"subject_type": "role", "subject_id": role, "display_name": "Coming soon"},
        headers=api.admin,
    )
    assert response.status_code == 201 and response.json()["display_name"] == "Coming soon"
    subjects = (await api.client.get("/api/permissions/subjects", headers=api.admin)).json()
    assert any(s["subject_id"] == role and s["subject_type"] == "role" for s in subjects)


async def test_anyone_with_a_grant_appears_even_if_never_seen(api):
    role = f"granted-only-{uuid4().hex[:6]}"
    await change_tables(api, await connection_id(api), "role", role, orders=True)
    subjects = (await api.client.get("/api/permissions/subjects", headers=api.admin)).json()
    assert any(s["subject_id"] == role for s in subjects)


async def test_the_seeded_analyst_role_is_listed(api):
    subjects = (await api.client.get("/api/permissions/subjects", headers=api.admin)).json()
    assert any(s["subject_type"] == "role" and s["subject_id"] == "analyst" for s in subjects)


# --- the table grid ------------------------------------------------------------------------------------


async def test_the_grid_lists_every_table_in_the_database_and_who_has_which(api):
    cid = await connection_id(api)
    grid = (
        await api.client.get(
            "/api/permissions/tables", params={"connection_id": cid}, headers=api.admin
        )
    ).json()

    assert (
        grid["reachable"] is True and grid["error"] is None and grid["connection_name"] == "shop-pg"
    )
    assert [t["name"] for t in grid["tables"]] == [
        "categories",
        "customers",
        "order_items",
        "orders",
        "payments",
        "products",
        "reviews",
    ]
    granted = {
        g["table"]
        for g in grid["grants"]
        if (g["subject_type"], g["subject_id"]) == ("role", "analyst")
    }
    assert granted == {
        "categories",
        "customers",
        "order_items",
        "orders",
        "products",
        "reviews",
    }  # not payments
    assert ["role", "analyst"] in grid["with_connection_access"]


async def test_descriptions_are_shown_beside_the_tables(api):
    cid = await connection_id(api)
    grid = (
        await api.client.get(
            "/api/permissions/tables", params={"connection_id": cid}, headers=api.admin
        )
    ).json()
    orders = next(t for t in grid["tables"] if t["name"] == "orders")
    assert "checkout" in orders["description"]


async def test_an_unreachable_database_still_lets_you_review_and_revoke(api, postgres):
    created = (
        await api.client.post(
            "/api/connections",
            json={
                "name": f"down-{uuid4().hex[:6]}",
                "engine": "postgresql",
                "details": {"host": postgres.host, "port": 1, "database": "x", "username": "u"},
                "secret": "pw",
            },
            headers=api.admin,
        )
    ).json()
    await change_tables(api, created["id"], "role", "reviewer", orders=True)

    grid = (
        await api.client.get(
            "/api/permissions/tables", params={"connection_id": created["id"]}, headers=api.admin
        )
    ).json()
    assert grid["reachable"] is False and "unavailable" in grid["error"]
    assert [t["name"] for t in grid["tables"]] == [
        "orders"
    ]  # what has been granted is still visible


async def test_the_grid_for_an_unknown_connection_is_a_404(api):
    response = await api.client.get(
        "/api/permissions/tables",
        params={"connection_id": "00000000-0000-0000-0000-000000000000"},
        headers=api.admin,
    )
    assert response.status_code == 404


# --- changing table access -------------------------------------------------------------------------------


async def test_granting_and_revoking_returns_the_subjects_current_grants(api):
    cid, role = await connection_id(api), f"r-{uuid4().hex[:6]}"
    after_grant = await change_tables(api, cid, "role", role, orders=True, customers=True)
    assert [g["table"] for g in after_grant] == ["customers", "orders"]
    after_revoke = await change_tables(api, cid, "role", role, customers=False)
    assert [g["table"] for g in after_revoke] == ["orders"]


async def test_repeating_a_change_is_harmless(api):
    cid, role = await connection_id(api), f"r-{uuid4().hex[:6]}"
    await change_tables(api, cid, "role", role, orders=True)
    assert len(await change_tables(api, cid, "role", role, orders=True)) == 1
    assert await change_tables(api, cid, "role", role, payments=False) == [
        {"subject_type": "role", "subject_id": role, "table": "orders"}
    ]


async def test_every_change_is_written_to_the_admin_log_with_who_did_it(api):
    cid, role = await connection_id(api), f"logged-{uuid4().hex[:6]}"
    await change_tables(api, cid, "role", role, orders=True, payments=True)
    await change_tables(api, cid, "role", role, payments=False)

    log = (
        await api.client.get(
            "/api/admin-log", params={"action": "permission.tables"}, headers=api.admin
        )
    ).json()
    mine = [i for i in log["items"] if i["details"]["subject"] == f"role:{role}"]
    assert [(m["details"]["granted"], m["details"]["revoked"]) for m in mine] == [
        ([], ["payments"]),
        (["orders", "payments"], []),
    ]
    assert mine[0]["actor_sub"] == "admin-1" and mine[0]["actor_name"] == "Ada Admin"
    assert mine[0]["target"] == "shop-pg"


@pytest.mark.parametrize(
    "body",
    [
        {"changes": []},
        {"changes": [{"table": "", "granted": True}]},
        {"changes": [{"table": "x" * 301, "granted": True}]},
        {"subject_type": "group"},
        {"subject_id": ""},
    ],
)
async def test_malformed_changes_are_refused(api, body):
    cid = await connection_id(api)
    good = {
        "connection_id": cid,
        "subject_type": "role",
        "subject_id": "r",
        "changes": [{"table": "t", "granted": True}],
    }
    response = await api.client.put(
        "/api/permissions/tables", json={**good, **body}, headers=api.admin
    )
    assert response.status_code == 422


async def test_changing_access_on_an_unknown_connection_is_a_404(api):
    response = await api.client.put(
        "/api/permissions/tables",
        json={
            "connection_id": "00000000-0000-0000-0000-000000000000",
            "subject_type": "role",
            "subject_id": "r",
            "changes": [{"table": "t", "granted": True}],
        },
        headers=api.admin,
    )
    assert response.status_code == 404


# --- the tool grid --------------------------------------------------------------------------------------


async def test_the_tool_grid_describes_all_eight_tools(api):
    grid = (await api.client.get("/api/permissions/tools", headers=api.admin)).json()
    assert [t["name"] for t in grid["tools"]] == [
        "list_connections",
        "list_tables",
        "describe_table",
        "search_schema",
        "get_relationships",
        "run_query",
        "explain_query",
        "get_sample_rows",
    ]
    assert all(t["description"] for t in grid["tools"])
    assert {("role", "analyst", "run_query")} <= {
        (g["subject_type"], g["subject_id"], g["tool"]) for g in grid["grants"]
    }


async def test_tools_can_be_granted_and_revoked(api):
    role = f"tools-{uuid4().hex[:6]}"
    put = lambda changes: api.client.put(  # noqa: E731
        "/api/permissions/tools",
        json={"subject_type": "role", "subject_id": role, "changes": changes},
        headers=api.admin,
    )
    granted = (
        await put(
            [{"tool": "list_tables", "granted": True}, {"tool": "run_query", "granted": True}]
        )
    ).json()
    assert [g["tool"] for g in granted] == ["list_tables", "run_query"]
    revoked = (await put([{"tool": "run_query", "granted": False}])).json()
    assert [g["tool"] for g in revoked] == ["list_tables"]


async def test_an_unknown_tool_is_refused(api):
    response = await api.client.put(
        "/api/permissions/tools",
        json={
            "subject_type": "role",
            "subject_id": "r",
            "changes": [{"tool": "drop_everything", "granted": True}],
        },
        headers=api.admin,
    )
    assert response.status_code == 422 and "drop_everything" in response.text


# --- the MCP server sees every change at once -------------------------------------------------------------


async def test_a_change_made_in_the_gui_is_effective_on_the_mcp_server_immediately(api, mcp):
    cid, role = await connection_id(api), f"live-{uuid4().hex[:6]}"
    who = Caller(sub=f"live-user-{uuid4().hex[:6]}", roles=frozenset({role}))
    sql = "SELECT count(*) FROM orders"

    # Nothing granted yet: the MCP server refuses.
    with pytest.raises(ToolNotPermitted):
        await mcp.query.run_query(who, "shop-pg", sql)

    # The admin grants the tool, the connection and the table through the API...
    await api.client.put(
        "/api/permissions/tools",
        json={
            "subject_type": "role",
            "subject_id": role,
            "changes": [{"tool": "run_query", "granted": True}],
        },
        headers=api.admin,
    )
    await api.client.put(
        f"/api/connections/{cid}/access",
        json={
            "subjects": [
                {"subject_type": "role", "subject_id": role},
                {"subject_type": "role", "subject_id": "analyst"},
            ]
        },
        headers=api.admin,
    )
    await change_tables(api, cid, "role", role, orders=True)
    assert (await mcp.query.run_query(who, "shop-pg", sql)).rows == [
        [70]
    ]  # ...and it works at once

    # ...then revokes the table. The MCP server has the old answer cached, but the change was
    # announced, so the very next request is refused.
    await change_tables(api, cid, "role", role, orders=False)
    with pytest.raises(TableNotFound):
        await mcp.query.run_query(who, "shop-pg", sql)

    # ...and revokes the tool.
    await api.client.put(
        "/api/permissions/tools",
        json={
            "subject_type": "role",
            "subject_id": role,
            "changes": [{"tool": "run_query", "granted": False}],
        },
        headers=api.admin,
    )
    with pytest.raises(ToolNotPermitted):
        await mcp.query.run_query(who, "shop-pg", sql)


async def test_every_permission_change_invalidates_the_mcp_servers_caches(api):
    cid, role = await connection_id(api), f"inv-{uuid4().hex[:6]}"
    before = await api.cache.version(META)
    await change_tables(api, cid, "role", role, orders=True)
    await api.client.put(
        "/api/permissions/tools",
        json={
            "subject_type": "role",
            "subject_id": role,
            "changes": [{"tool": "list_tables", "granted": True}],
        },
        headers=api.admin,
    )
    assert await api.cache.version(META) == before + 2
