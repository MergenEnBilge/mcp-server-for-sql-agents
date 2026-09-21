"""The real server process, launched the way Claude Desktop would launch it, over stdio.

Nothing is faked: a subprocess runs `python -m mcp_sql_server`, connects to the real
app_meta database, and answers for both the Postgres and the SQLite sample databases.
"""

import subprocess
import sys
import uuid

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from sqlalchemy import text

REGISTERED = pytest.mark.usefixtures("registered")


@pytest.fixture(params=["shop-pg", "shop-sqlite"])
def conn(request) -> str:
    return request.param


def server_env(postgres, fernet_key: str, sub: str, sqlite_file) -> dict[str, str]:
    return {
        "MCP_SQLITE_ROOT": str(sqlite_file.parent),
        "MCP_APP_META_URL": postgres.url("mcp_app", "app_meta"),
        "MCP_CONNECTION_SECRET_KEYS": fernet_key,
        "MCP_STDIO_SUB": sub,
        "MCP_STDIO_NAME": "Stdio Tester",
        "MCP_STDIO_ROLES": "analyst",
        "MCP_DEFAULT_QUERY_TIMEOUT_S": "2",
    }


@pytest.fixture
def stdio_params(postgres, fernet_key, tmp_path, sqlite_file):
    sub = f"stdio-{uuid.uuid4().hex[:8]}"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_sql_server"],
        env=server_env(postgres, fernet_key, sub, sqlite_file),
        cwd=str(tmp_path),  # no stray .env file to pick up
    )


@REGISTERED
async def test_a_real_client_can_explore_and_query_over_stdio(stdio_params, conn):
    async with Client(stdio_params) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert {"list_connections", "run_query", "describe_table"} <= tools

        listed = await client.call_tool("list_connections", {})
        assert {c["name"] for c in listed.structured_content["result"]} >= {
            "shop-pg",
            "shop-sqlite",
        }

        tables = await client.call_tool("list_tables", {"connection_name": conn})
        assert "orders" in {t["name"] for t in tables.structured_content["result"]}
        assert "payments" not in {t["name"] for t in tables.structured_content["result"]}

        described = await client.call_tool(
            "describe_table", {"connection_name": conn, "table_name": "orders"}
        )
        assert described.structured_content["foreign_keys"][0]["to_table"] == "customers"

        result = await client.call_tool(
            "run_query",
            {"connection_name": conn, "sql": "SELECT count(*) AS n FROM orders"},
        )
        assert not result.is_error
        assert result.structured_content["rows"] == [[70]]


@REGISTERED
async def test_refusals_and_untrusted_text_survive_the_trip_over_stdio(stdio_params, conn):
    async with Client(stdio_params) as client:
        denied = await client.call_tool(
            "run_query", {"connection_name": conn, "sql": "SELECT * FROM payments"}
        )
        assert denied.is_error

        write = await client.call_tool(
            "run_query", {"connection_name": conn, "sql": "DELETE FROM reviews"}
        )
        assert write.is_error

        reviews = await client.call_tool(
            "run_query",
            {"connection_name": conn, "sql": "SELECT id, body FROM reviews WHERE id IN (31, 32)"},
        )
        body = str(reviews.structured_content["rows"]).lower()
        assert "ignore all previous" not in body
        assert "drop table" not in body
        assert len(reviews.structured_content["security_flags"]) == 2


@REGISTERED
async def test_the_stdio_identity_is_what_shows_up_in_the_audit_log(stdio_params, stack):
    async with Client(stdio_params) as client:
        await client.call_tool("list_tables", {"connection_name": "shop-pg"})

    async with stack.admin.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT caller_name, tool_name, success FROM audit_log WHERE caller_sub = :sub"
                ),
                {"sub": stdio_params.env["MCP_STDIO_SUB"]},
            )
        ).all()
    assert [tuple(r) for r in rows] == [("Stdio Tester", "list_tables", True)]


def test_stdio_refuses_to_start_without_an_identity(postgres, fernet_key, tmp_path, sqlite_file):
    env = server_env(postgres, fernet_key, "", sqlite_file)
    env.pop("MCP_STDIO_SUB")
    done = subprocess.run(
        [sys.executable, "-m", "mcp_sql_server"],
        env={**env, "PATH": "", "SYSTEMROOT": "C:\\Windows"},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode != 0
    assert "MCP_STDIO_SUB" in done.stderr
