"""Registers an AI client the moment it connects, not only when it first calls a tool.

A client introduces itself, and that is the "first connection": we want the administrator to see
the request straight away, with a name they can recognise, instead of waiting for the agent to
try a tool. How it introduces itself depends on the protocol revision:

  * older clients open with an `initialize` request whose params carry `clientInfo`;
  * newer ones (protocol 2026-07-28) have no handshake and put the same information under
    `params._meta["io.modelcontextprotocol/clientInfo"]` on every request.

The name is what the client says about itself, so it is only ever shown as a hint; the identity
that counts is the OAuth client id in the access token, which the identity provider set.

This wraps the whole HTTP app and peeks at request bodies. Nothing is changed or held back, a
failure here never affects the request, and a token is only looked at once a minute.
"""

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mcp_sql_server.auth.caller import caller_from_claims
from mcp_sql_server.models import Caller

logger = logging.getLogger(__name__)

CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"

_MAX_PEEK_BYTES = 64 * 1024  # bigger bodies aren't an introduction; the tool call registers it
_SEEN_FOR_S = 60.0
_MAX_REMEMBERED = 2000

NoteClient = Callable[[Caller, str | None], Awaitable[None]]


class RegisterOnInitialize:
    def __init__(
        self,
        app: ASGIApp,
        verifier: TokenVerifier,
        note_client: NoteClient,
        roles_claim: str,
        path: str = "/mcp",
    ) -> None:
        self.app = app
        self._verifier = verifier
        self._note_client = note_client
        self._roles_claim = roles_claim
        self._path = path
        self._seen_until: dict[str, float] = {}  # sha256 of a token -> when to look again

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = self._token_worth_checking(scope)
        if token is None:
            await self.app(scope, receive, send)
            return

        # Read the body, then hand the same bytes on to the real app.
        buffered: list[Message] = []
        body = b""
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body", False) or len(body) > _MAX_PEEK_BYTES:
                break

        async def replay() -> Message:
            return buffered.pop(0) if buffered else await receive()

        try:
            await self._register(token, body)
        except Exception:
            logger.exception("could not register the connecting agent")
        await self.app(scope, replay, send)

    def _token_worth_checking(self, scope: Scope) -> str | None:
        """The bearer token of a JSON POST to the MCP endpoint that we haven't looked at lately."""
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self._path:
            return None
        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        if "json" not in headers.get("content-type", "") or authorization[:7].lower() != "bearer ":
            return None
        token = authorization[7:]
        return None if self._seen_until.get(_digest(token), 0.0) > time.monotonic() else token

    async def _register(self, token: str, body: bytes) -> None:
        if len(body) > _MAX_PEEK_BYTES:
            return
        try:
            request: Any = json.loads(body)
        except ValueError:
            return
        if not isinstance(request, dict):
            return

        access = await self._verifier.verify_token(token)
        if access is None or not access.claims:
            return  # the real app will answer 401; there's nothing to register
        caller = caller_from_claims(access.claims, self._roles_claim)
        if caller.client_id is None:
            return
        await self._note_client(caller, _reported_name(request))
        self._remember(token)

    def _remember(self, token: str) -> None:
        now = time.monotonic()
        if len(self._seen_until) >= _MAX_REMEMBERED:
            self._seen_until = {k: t for k, t in self._seen_until.items() if t > now}
        if len(self._seen_until) < _MAX_REMEMBERED:
            self._seen_until[_digest(token)] = now + _SEEN_FOR_S


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _reported_name(request: dict[str, Any]) -> str | None:
    """ "Name version" from whichever place this protocol revision puts the client's info."""
    params = request.get("params")
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    info = params.get("clientInfo") if request.get("method") == "initialize" else None
    if info is None and isinstance(meta, dict):
        info = meta.get(CLIENT_INFO_META_KEY)
    if not isinstance(info, dict):
        return None
    return " ".join(str(info[key]) for key in ("name", "version") if info.get(key)) or None
