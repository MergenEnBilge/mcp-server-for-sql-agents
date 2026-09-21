"""The admin GUI's REST API.

    uvicorn gui_backend.main:app --host 0.0.0.0 --port 8100

Stateless, like the MCP server: identity comes from the Bearer token on each request and
everything else lives in Postgres or Redis, so any number of replicas can run side by side.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.auth.provider import TokenVerifier
from sqlalchemy import text

from gui_backend.config import Settings, get_settings
from gui_backend.context import Context, build_context
from gui_backend.routers import audit, connections, me, permissions, schema
from mcp_sql_server.cache.base import Cache

logger = logging.getLogger("gui_backend")


def create_app(
    settings: Settings | None = None,
    *,
    cache: Cache | None = None,
    verifier: TokenVerifier | None = None,
) -> FastAPI:
    """Build the application. `cache` and `verifier` can be supplied by tests."""
    settings = settings or get_settings()
    ctx = build_context(settings, cache=cache, verifier=verifier)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await ctx.close()

    app = FastAPI(title="SQL data layer: admin API", lifespan=lifespan)
    app.state.ctx = ctx

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    for module in (me, audit, connections, permissions, schema):
        app.include_router(module.router)

    _add_health_routes(app, ctx)
    return app


def _add_health_routes(app: FastAPI, ctx: Context) -> None:
    @app.get("/healthz", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        """Is the process up? Public and dependency-free, for load balancers."""
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readiness() -> JSONResponse:
        """Can it serve requests? Checks the database, and says nothing else about it."""
        try:
            async with ctx.engine.connect() as db:
                await db.execute(text("SELECT 1"))
        except Exception:
            logger.exception("readiness check failed")
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory gui_backend.main:app_factory`."""
    logging.basicConfig(level=logging.INFO)
    return create_app()
