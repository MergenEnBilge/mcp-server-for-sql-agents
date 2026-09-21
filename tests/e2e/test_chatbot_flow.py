"""An online chatbot connecting to the running stack, end to end, with a real sign-in.

These need the whole stack running with the demo users (docker compose --profile demo up) and are
skipped unless E2E_BASE_URL points at it:

    E2E_BASE_URL=https://localhost python -m pytest tests/e2e

The chatbot is simulated by deploy/verify_chatbot_flow.py, which does what one does: finds the
sign-in details from the server's own discovery documents, registers itself as a client, signs a
person in (with PKCE and the consent screen), and speaks MCP with the token.
"""

import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
import verify_chatbot_flow as flow  # noqa: E402

BASE = os.environ.get("E2E_BASE_URL", "").rstrip("/")
PASSWORD = os.environ.get("KEYCLOAK_DEV_USER_PASSWORD", "")

pytestmark = pytest.mark.skipif(
    not (BASE and PASSWORD), reason="set E2E_BASE_URL and KEYCLOAK_DEV_USER_PASSWORD to run"
)

MCP_URL = f"{BASE}/mcp"


@pytest.fixture
def chatbot(admin_api):
    """A chatbot that has just connected as bob. What it left behind (the OAuth client it
    registered, and its entry in the admin console) is removed afterwards, so it can't turn up as
    a pop-up in someone's console later."""
    outcome = flow.connect_like_a_chatbot(MCP_URL, "bob", PASSWORD)
    yield outcome
    outcome.close()
    for agent in admin_api.get("/agents").json():
        if agent["client_id"] == outcome.client_id:
            admin_api.delete(f"/agents/{agent['id']}")


@pytest.fixture
def admin_api():
    """The admin API, as alice, through the same public address."""
    tokens = httpx.post(
        f"{BASE}/auth/realms/sql-data-layer/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "e2e-test",
            "username": "alice",
            "password": PASSWORD,
        },
        verify=False,
    ).json()
    with httpx.Client(
        base_url=f"{BASE}/api",
        verify=False,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    ) as client:
        yield client


# --- the chain a chatbot follows -------------------------------------------------------------------------


def test_a_chatbot_can_find_register_sign_in_and_talk_to_the_server(chatbot):
    assert chatbot.steps[:3] == [
        f"found the sign-in details ({chatbot.metadata_found_at})",
        f"registered itself as client {chatbot.client_id}",
        "signed in and accepted the consent screen",
    ]
    assert chatbot.consent_screen_shown, "a newly registered client must be approved by the person"


def test_the_sign_in_details_are_found_where_the_specification_says_to_look(chatbot):
    # RFC 8414 with the issuer's path inserted, which is where chatbots look first.
    assert "/.well-known/oauth-authorization-server/auth/realms/" in chatbot.metadata_found_at


def test_the_token_is_for_this_server_and_names_the_client_and_the_persons_roles(chatbot):
    claims = chatbot.token_claims
    audiences = [claims["aud"]] if isinstance(claims["aud"], str) else claims["aud"]
    assert MCP_URL in audiences
    assert claims["azp"] == chatbot.client_id
    # Roles reach a self-registered client's token, so the person's own permissions apply.
    assert "analyst" in claims["realm_access"]["roles"]
    # ...but not every role the identity provider has.
    assert "offline_access" not in claims["realm_access"]["roles"]


def test_a_new_chatbot_starts_out_waiting_for_an_administrator(chatbot):
    assert chatbot.my_access["status"] == "pending_approval"
    assert chatbot.my_access["tools"] == []
    assert "administrator" in chatbot.my_access["message"]
    assert "get_my_access" in chatbot.server_instructions


def test_the_agent_appears_for_the_administrator_and_works_once_allowed(chatbot, admin_api):
    pending = admin_api.get("/agents/pending").json()["agents"]
    mine = next(a for a in pending if a["client_id"] == chatbot.client_id)
    assert mine["reported_name"] == "Chatbot simulator 1.0"
    assert mine["last_user_name"] == "Bob Analyst"

    http = httpx.Client(verify=False)
    try:
        refused = flow.call_tool(http, MCP_URL, chatbot.access_token, "list_connections")
        assert refused["isError"] and "not been approved" in refused["content"][0]["text"]

        approved = admin_api.post(
            f"/agents/{mine['id']}/approve",
            json={
                "label": "Simulated chatbot",
                "tools": ["list_connections", "list_tables"],
                "all_connections": True,
                "connection_ids": [],
                "expires_in_hours": 1,
            },
        )
        assert approved.status_code == 200

        allowed = flow.call_tool(http, MCP_URL, chatbot.access_token, "list_connections")
        assert not allowed["isError"]
        access = flow.call_tool(http, MCP_URL, chatbot.access_token, "get_my_access")
        assert access["structuredContent"]["status"] == "ready"
        query = flow.call_tool(
            http,
            MCP_URL,
            chatbot.access_token,
            "run_query",
            {"connection_name": "shop-sqlite", "sql": "SELECT 1"},
        )
        assert query["isError"] and "This agent is not permitted" in query["content"][0]["text"]
    finally:
        http.close()


# --- what a chatbot must not be able to do -----------------------------------------------------------------


@pytest.fixture
def http():
    with httpx.Client(verify=False, follow_redirects=False, timeout=30) as client:
        yield client


@pytest.fixture
def metadata(http):
    return flow.discover(http, MCP_URL)[2]


def test_a_client_cannot_register_a_redirect_address_on_an_unknown_site(http, metadata):
    refused = flow.register(http, metadata, ["https://evil.example/steal"], "Impostor")
    assert refused.status_code in (400, 403), refused.text


def test_one_unknown_address_among_good_ones_is_enough_to_refuse_it(http, metadata):
    mixed = [flow.DEFAULT_REDIRECT, "https://evil.example/steal"]
    assert flow.register(http, metadata, mixed, "Impostor").status_code in (400, 403)


def registered(http, metadata):
    answer = flow.register(http, metadata, [flow.DEFAULT_REDIRECT], "PKCE probe")
    assert answer.status_code == 201, answer.text
    return answer.json()


def forget(http, client):
    http.delete(
        client["registration_client_uri"],
        headers={"Authorization": f"Bearer {client['registration_access_token']}"},
    )


def test_signing_in_without_pkce_is_refused(http, metadata):
    client = registered(http, metadata)
    try:
        url = flow.authorization_url(
            metadata, client["client_id"], flow.DEFAULT_REDIRECT, MCP_URL, {}
        )
        page = http.get(url)
        # Either an error page, or an error sent back to the redirect address; never a login form.
        assert "kc-form-login" not in page.text
        assert page.status_code >= 300
    finally:
        forget(http, client)


def test_the_weak_plain_pkce_method_is_refused(http, metadata):
    client = registered(http, metadata)
    try:
        url = flow.authorization_url(
            metadata,
            client["client_id"],
            flow.DEFAULT_REDIRECT,
            MCP_URL,
            {"code_challenge": "x" * 43, "code_challenge_method": "plain"},
        )
        page = http.get(url)
        assert "kc-form-login" not in page.text
    finally:
        forget(http, client)


def test_a_token_meant_for_another_service_is_refused_by_the_mcp_server(http):
    """The gui client's token names the admin API, not this server."""
    token = httpx.post(
        f"{BASE}/auth/realms/sql-data-layer/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "gui",
            "username": "bob",
            "password": PASSWORD,
        },
        verify=False,
    )
    # The console's own client doesn't allow the password grant at all; if it ever did, its
    # token still wouldn't be accepted here.
    if token.status_code == 200:
        answer = http.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": f"Bearer {token.json()['access_token']}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert answer.status_code == 401
    else:
        assert token.status_code in (400, 401)
