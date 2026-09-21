"""Registering an AI client as it introduces itself, in either protocol revision, without
disturbing the request."""

import json

import httpx
import pytest
from mcp.server.auth.provider import AccessToken

from mcp_sql_server.auth.agent_registration import CLIENT_INFO_META_KEY, RegisterOnInitialize

GOOD = "good-token"


class Verifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify_token(self, token: str) -> AccessToken | None:
        self.calls += 1
        if token != GOOD:
            return None
        return AccessToken(
            token=token,
            client_id="chat-1",
            scopes=[],
            claims={"sub": "person-1", "name": "Pat", "azp": "chat-1"},
        )


class Recorder:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str | None]] = []
        self.bodies: list[bytes] = []

    async def note(self, caller, name) -> None:
        self.seen.append((caller.client_id, caller.sub, name))

    async def app(self, scope, receive, send) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.bodies.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture
def wired():
    recorder, verifier = Recorder(), Verifier()
    app = RegisterOnInitialize(recorder.app, verifier, recorder.note, "realm_access.roles")
    return recorder, verifier, app


async def post(app, body, *, token=GOOD, path="/mcp", content_type="application/json"):
    headers = {"content-type": content_type}
    if token:
        headers["authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        content = body if isinstance(body, bytes) else json.dumps(body).encode()
        return await c.post(path, content=content, headers=headers)


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"clientInfo": {"name": "ChatApp", "version": "2.0"}},
}
MODERN = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {"_meta": {CLIENT_INFO_META_KEY: {"name": "NewChat", "version": "9"}}},
}


async def test_an_initialize_request_registers_the_client_with_its_own_name(wired):
    recorder, _, app = wired
    response = await post(app, INITIALIZE)
    assert response.text == "ok"
    assert recorder.seen == [("chat-1", "person-1", "ChatApp 2.0")]


async def test_a_newer_client_is_registered_from_the_metadata_of_any_request(wired):
    recorder, _, app = wired
    await post(app, MODERN)
    assert recorder.seen == [("chat-1", "person-1", "NewChat 9")]


async def test_the_request_reaches_the_server_byte_for_byte(wired):
    recorder, _, app = wired
    payload = json.dumps(INITIALIZE).encode()
    await post(app, payload)
    assert recorder.bodies == [payload]


async def test_a_token_is_only_looked_at_once_in_a_while(wired):
    recorder, verifier, app = wired
    for _ in range(5):
        await post(app, MODERN)
    assert len(recorder.seen) == 1
    assert verifier.calls == 1


async def test_a_request_with_no_name_still_registers_the_client(wired):
    recorder, _, app = wired
    await post(app, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert recorder.seen == [("chat-1", "person-1", None)]


async def test_a_token_that_does_not_verify_registers_nothing(wired):
    recorder, _, app = wired
    response = await post(app, INITIALIZE, token="forged")
    assert response.text == "ok"  # the real app decides what to answer
    assert recorder.seen == []


@pytest.mark.parametrize(
    ("kwargs", "body"),
    [
        ({"token": None}, INITIALIZE),
        ({"path": "/elsewhere"}, INITIALIZE),
        ({"content_type": "text/plain"}, INITIALIZE),
        ({}, b"not json at all"),
        ({}, [1, 2, 3]),
    ],
    ids=["no-token", "other-path", "not-json-type", "garbage-body", "not-an-object"],
)
async def test_anything_else_passes_straight_through(wired, kwargs, body):
    recorder, verifier, app = wired
    response = await post(app, body, **kwargs)
    assert response.status_code == 200
    assert recorder.seen == []
    assert len(recorder.bodies) == 1


async def test_a_failure_while_registering_never_breaks_the_request(wired):
    recorder, _, app = wired

    async def broken(caller, name):
        raise RuntimeError("database is down")

    app._note_client = broken
    response = await post(app, INITIALIZE)
    assert response.text == "ok"
    assert len(recorder.bodies) == 1
