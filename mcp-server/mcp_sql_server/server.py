"""Builds and runs the MCP server.

    python -m mcp_sql_server                       # stdio, for Claude Desktop and similar
    python -m mcp_sql_server --transport http      # Streamable HTTP with OAuth 2.1

Every transport serves the same eight tools over the same services; transports differ only
in how the caller's identity is established (see auth/caller.py).
"""

import argparse
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_sql_server.auth.caller import CallerProvider, stdio_caller, token_caller
from mcp_sql_server.cache.base import Cache
from mcp_sql_server.config import Settings, get_settings, split_list
from mcp_sql_server.container import Services, build_services
from mcp_sql_server.tools.mcp_tools import INSTRUCTIONS, register_tools

logger = logging.getLogger("mcp_sql_server")

SERVER_NAME = "sql-data-layer"


def create_server(
    services: Services,
    current_caller: CallerProvider,
    *,
    auth: AuthSettings | None = None,
    token_verifier: TokenVerifier | None = None,
    close_services: bool = True,
) -> MCPServer[Any]:
    """An MCP server with all tools registered.

    `close_services` says whether shutting the server down should also close the database
    connections in `services` (yes in production; tests that share services say no).
    """

    @asynccontextmanager
    async def lifespan(_server: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if close_services:
                await services.close()

    server: MCPServer[Any] = MCPServer(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        auth=auth,
        token_verifier=token_verifier,
        lifespan=lifespan,
    )
    register_tools(server, services, current_caller)

    @server.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
    async def healthz(_request: Request) -> JSONResponse:
        # Deliberately public and dependency-free: it answers "is the process up?", which is
        # all a load balancer needs. It says nothing about the databases.
        return JSONResponse({"status": "ok"})

    return server


def build_http_auth(
    settings: Settings, verifier: TokenVerifier | None = None, cache: Cache | None = None
) -> tuple[AuthSettings, TokenVerifier]:
    """OAuth 2.1 resource-server settings and the verifier for HTTP requests.

    A ready-made `verifier` can be passed in (tests do, to avoid real network calls).
    """
    from mcp_sql_server.auth.token_verifier import build_token_verifier

    if not settings.public_url or not settings.oauth_issuer:
        raise ValueError(
            "The HTTP transport needs MCP_PUBLIC_URL (this server's address) and "
            "MCP_OAUTH_ISSUER (the identity provider's issuer URL)."
        )
    auth = AuthSettings(
        issuer_url=settings.oauth_issuer,
        resource_server_url=settings.public_url,
        required_scopes=split_list(settings.oauth_required_scopes) or None,
        # Our verifier checks the token's audience itself (see token_verifier.py).
        validate_token_resource=False,
    )
    return auth, verifier or build_token_verifier(settings, cache)


def create_http_app(
    settings: Settings, services: Services, verifier: TokenVerifier | None = None
) -> Starlette:
    """The Streamable HTTP application: OAuth-protected MCP endpoint plus public metadata."""
    auth, verifier = build_http_auth(settings, verifier, services.cache)
    server = create_server(
        services, token_caller(settings.oauth_roles_claim), auth=auth, token_verifier=verifier
    )
    # Stateless: no session lives in this process, so any number of replicas can sit behind a
    # load balancer with no sticky sessions.
    return server.streamable_http_app(
        stateless_http=True, json_response=True, host=settings.http_host
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mcp_sql_server", description=__doc__.split("\n\n")[0])
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    args = parser.parse_args(argv)

    # stdout belongs to the MCP protocol on stdio, so logs must go to stderr.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    services = build_services(settings)

    if args.transport == "stdio":
        create_server(services, stdio_caller(settings)).run("stdio")
        return

    import uvicorn

    uvicorn.run(
        create_http_app(settings, services),
        host=settings.http_host,
        port=settings.http_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
