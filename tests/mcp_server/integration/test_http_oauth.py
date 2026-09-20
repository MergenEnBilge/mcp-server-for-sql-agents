"""The Streamable HTTP transport with OAuth 2.1, served by a real uvicorn process-in-a-thread
and backed by the real databases. Tokens come from a fake identity provider (fake_idp.py), so
these tests prove what the *server* does with tokens, not what an identity provider does."""

import socket
import threading
import time
import uuid
from contextlib import contextmanager

import httpx
import httpx2
import pytest
import uvicorn
from fake_idp import AUDIENCE, ISSUER, FakeIdP
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text

from mcp_sql_server.auth.token_verifier import JwtTokenVerifier
from mcp_sql_server.config import Settings
from mcp_sql_server.container import build_services
from mcp_sql_server.server import create_http_app

REGISTERED = pytest.mark.usefixtures("registered")


class RunningServer:
    def __init__(self, url: str, idp: FakeIdP) -> None:
        self.url = url  # e.g. http://127.0.0.1:5555
        self.mcp_url = f"{url}/mcp"
        self.idp = idp


@contextmanager
def serve(postgres, fernet_key: str, idp: FakeIdP, **overrides):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(
        _env_file=None,
        app_meta_url=postgres.url("mcp_app", "app_meta"),
        connection_secret_keys=fernet_key,
        public_url=f"http://127.0.0.1:{port}/mcp",
        oauth_issuer=ISSUER,
        oauth_audience=AUDIENCE,
        default_query_timeout_s=2,
        **overrides,
    )
    verifier = JwtTokenVerifier(issuer=ISSUER, audience=AUDIENCE, http_client=idp.http_client())
    app = create_http_app(settings, build_services(settings), verifier)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    try:
        yield RunningServer(f"http://127.0.0.1:{port}", idp)
    finally:
        server.should_exit = True
        thread.join(timeout=15)


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def running(postgres, fernet_key, idp):
    with serve(postgres, fernet_key, idp) as s:
        yield s


def mcp_client(server: RunningServer, token: str) -> Client:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client(server.mcp_url, http_client=http))


def initialize_request() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }


MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


# --- what an unauthenticated client sees -------------------------------------------------------


@REGISTERED
def test_without_a_token_the_server_says_where_to_get_one(running):
    response = httpx.post(running.mcp_url, json=initialize_request(), headers=MCP_HEADERS)

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer")
    assert "resource_metadata=" in challenge
    assert "/.well-known/oauth-protected-resource" in challenge


@REGISTERED
def test_the_protected_resource_metadata_names_this_server_and_the_authorization_server(running):
    metadata = httpx.get(f"{running.url}/.well-known/oauth-protected-resource/mcp").json()

    assert metadata["resource"] == running.mcp_url
    assert metadata["authorization_servers"] == [ISSUER]


@REGISTERED
def test_the_health_endpoint_is_public_and_says_nothing_about_the_databases(running):
    response = httpx.get(f"{running.url}/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@REGISTERED
@pytest.mark.parametrize(
    "make_token",
    [
        lambda idp: idp.token(expires_in=-3600),
        lambda idp: idp.token(aud="https://some-other-api.test"),
        lambda idp: idp.token(iss="https://evil.test/realms/shop"),
        lambda idp: FakeIdP().token(),  # signed with a key the server has never heard of
        lambda idp: "not-a-token",
    ],
    ids=["expired", "wrong-audience", "wrong-issuer", "unknown-signing-key", "garbage"],
)
def test_bad_tokens_are_turned_away_with_a_401(running, idp, make_token):
    response = httpx.post(
        running.mcp_url,
        json=initialize_request(),
        headers={**MCP_HEADERS, "Authorization": f"Bearer {make_token(idp)}"},
    )
    assert response.status_code == 401
    assert "invalid_token" in response.headers["www-authenticate"]


# --- what an authenticated client can do -------------------------------------------------------


@REGISTERED
@pytest.mark.parametrize("conn", ["shop-pg", "shop-sqlite"])
async def test_a_valid_token_gets_the_tools_and_real_answers(running, idp, conn):
    async with mcp_client(running, idp.token()) as client:
        assert "run_query" in {t.name for t in (await client.list_tools()).tools}
        result = await client.call_tool(
            "run_query", {"connection_name": conn, "sql": "SELECT count(*) FROM orders"}
        )
        assert not result.is_error
        assert result.structured_content["rows"] == [[70]]


@REGISTERED
async def test_the_identity_in_the_token_is_who_the_audit_log_names(running, idp, stack):
    sub = f"oidc-{uuid.uuid4().hex[:8]}"
    async with mcp_client(running, idp.token(sub=sub, name="Ana From Token")) as client:
        await client.call_tool("list_tables", {"connection_name": "shop-pg"})

    async with stack.admin.connect() as db:
        rows = (
            await db.execute(
                text("SELECT caller_name, tool_name, success FROM audit_log WHERE caller_sub = :s"),
                {"s": sub},
            )
        ).all()
    assert [tuple(r) for r in rows] == [("Ana From Token", "list_tables", True)]


@REGISTERED
async def test_what_a_caller_may_see_follows_the_roles_in_their_token(running, idp):
    async with mcp_client(running, idp.token(roles=("analyst",))) as client:
        allowed = await client.call_tool("list_tables", {"connection_name": "shop-pg"})
        assert not allowed.is_error

    async with mcp_client(running, idp.token(roles=("nobody",))) as client:
        denied = await client.call_tool("list_tables", {"connection_name": "shop-pg"})
        assert denied.is_error  # authenticated, but not granted anything
        listed = await client.call_tool("list_connections", {})
        assert listed.is_error  # not even granted the discovery tool


@REGISTERED
async def test_a_client_cannot_claim_a_different_identity_through_tool_arguments(running, idp):
    async with mcp_client(running, idp.token(roles=("nobody",))) as client:
        result = await client.call_tool(
            "run_query",
            {
                "connection_name": "shop-pg",
                "sql": "SELECT 1",
                "caller": "analyst",
                "roles": ["analyst"],
            },
        )
        assert result.is_error


# --- deployment properties ----------------------------------------------------------------------


@REGISTERED
async def test_any_replica_can_answer_any_request_with_no_sticky_sessions(
    postgres, fernet_key, idp
):
    token = idp.token()
    with (
        serve(postgres, fernet_key, idp) as first,
        serve(postgres, fernet_key, idp) as second,
    ):
        for server in (first, second, first, second):
            async with mcp_client(server, token) as client:
                result = await client.call_tool(
                    "run_query",
                    {"connection_name": "shop-sqlite", "sql": "SELECT count(*) FROM orders"},
                )
                assert result.structured_content["rows"] == [[70]]


@REGISTERED
async def test_required_scopes_are_enforced(postgres, fernet_key, idp):
    with serve(postgres, fernet_key, idp, oauth_required_scopes="mcp:query") as server:
        response = httpx.post(
            server.mcp_url,
            json=initialize_request(),
            headers={**MCP_HEADERS, "Authorization": f"Bearer {idp.token(scope='openid')}"},
        )
        assert response.status_code == 403
        assert "insufficient_scope" in response.headers["www-authenticate"]

        async with mcp_client(server, idp.token(scope="openid mcp:query")) as client:
            assert (await client.list_tools()).tools
