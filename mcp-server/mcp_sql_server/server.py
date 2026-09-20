"""Builds and runs the MCP server.

    python -m mcp_sql_server        # stdio, for Claude Desktop and similar

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
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_sql_server.auth.caller import CallerProvider, stdio_caller
from mcp_sql_server.config import get_settings
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mcp_sql_server", description=__doc__.split("\n\n")[0])
    parser.add_argument("--transport", choices=["stdio"], default="stdio")
    parser.parse_args(argv)

    # stdout belongs to the MCP protocol on stdio, so logs must go to stderr.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    services = build_services(settings)

    server = create_server(services, stdio_caller(settings))
    server.run("stdio")


if __name__ == "__main__":
    main()
