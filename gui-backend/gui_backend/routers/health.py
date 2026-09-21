"""What an operator needs to know at a glance: are the parts up, and how are calls going?

Everything about calls comes from the audit log, so there is no separate metrics store to keep in
step. "Errors" are calls that didn't succeed (refused SQL, timeouts, missing permissions and real
failures alike), and latency is measured on `run_query` alone, because listing tables is always
quick and would only flatter the number.
"""

import math
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.pool import QueuePool

from gui_backend.auth import Admin, ContextDep
from gui_backend.context import Context

router = APIRouter(prefix="/api", tags=["health"])

BUCKETS = 48  # points in each time series, whatever the window


class Check(BaseModel):
    state: Literal["ok", "down", "not_configured"]
    detail: str | None = None
    latency_ms: int | None = None


class PoolStatus(BaseModel):
    size: int
    in_use: int
    idle: int
    overflow: int


class Window(BaseModel):
    hours: int
    calls: int
    errors: int
    error_rate: float | None  # None when there were no calls, rather than a misleading 0
    query_calls: int
    p95_query_ms: int | None


class Bucket(BaseModel):
    start: datetime
    calls: int
    errors: int
    p95_query_ms: int | None


class ConnectionHealth(BaseModel):
    name: str
    engine: str
    is_active: bool
    last_checked_at: datetime | None
    last_check_ok: bool | None
    last_check_error: str | None


class Health(BaseModel):
    checked_at: datetime
    database: Check
    redis: Check
    mcp_server: Check
    pool: PoolStatus | None
    window: Window
    series: list[Bucket]
    connections: list[ConnectionHealth]


async def _database(ctx: Context) -> Check:
    started = time.monotonic()
    try:
        async with ctx.engine.connect() as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        return Check(state="down", detail=f"Could not query app_meta: {type(exc).__name__}")
    return Check(state="ok", latency_ms=int((time.monotonic() - started) * 1000))


async def _redis(ctx: Context) -> Check:
    if ctx.settings.redis_url is None:
        return Check(
            state="not_configured",
            detail="Running without Redis: nothing is cached and changes reach the MCP server on their own.",
        )
    started = time.monotonic()
    if not await ctx.cache.ping():
        return Check(
            state="down",
            detail="Redis does not answer. Everything still works, without caching.",
        )
    return Check(state="ok", latency_ms=int((time.monotonic() - started) * 1000))


async def _mcp_server(ctx: Context) -> Check:
    url = ctx.settings.mcp_health_url
    if not url:
        return Check(state="not_configured", detail="GUI_MCP_HEALTH_URL is not set.")
    started = time.monotonic()
    try:
        response = await ctx.http.get(url)
    except Exception as exc:
        return Check(state="down", detail=f"No answer from the MCP server ({type(exc).__name__}).")
    if response.status_code != 200:
        return Check(state="down", detail=f"The MCP server answered {response.status_code}.")
    return Check(state="ok", latency_ms=int((time.monotonic() - started) * 1000))


def _pool(ctx: Context) -> PoolStatus | None:
    pool = ctx.engine.sync_engine.pool
    if not isinstance(pool, QueuePool):
        return None
    return PoolStatus(
        size=pool.size(),
        in_use=pool.checkedout(),
        idle=pool.checkedin(),
        overflow=max(0, pool.overflow()),
    )


@router.get("/health")
async def health(
    ctx: ContextDep,
    _admin: Admin,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> Health:
    now = datetime.now(UTC)
    start = now - timedelta(hours=hours)
    step_s = max(60, int(hours * 3600 / BUCKETS))

    async with ctx.engine.connect() as db:
        totals = (
            (
                await db.execute(
                    text(
                        "SELECT count(*) AS calls, count(*) FILTER (WHERE NOT success) AS errors, "
                        "count(*) FILTER (WHERE tool_name = 'run_query') AS query_calls, "
                        "percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) "
                        "FILTER (WHERE tool_name = 'run_query') AS p95 "
                        "FROM audit_log WHERE occurred_at >= :start"
                    ),
                    {"start": start},
                )
            )
            .mappings()
            .one()
        )
        rows = (
            await db.execute(
                text(
                    "SELECT floor(extract(epoch FROM (occurred_at - :start)) / :step)::int AS n, "
                    "count(*) AS calls, count(*) FILTER (WHERE NOT success) AS errors, "
                    "percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) "
                    "FILTER (WHERE tool_name = 'run_query') AS p95 "
                    "FROM audit_log WHERE occurred_at >= :start GROUP BY 1"
                ),
                {"start": start, "step": step_s},
            )
        ).mappings()
        by_bucket = {r["n"]: r for r in rows}
        connections = [
            ConnectionHealth(**r)
            for r in (
                await db.execute(
                    text(
                        "SELECT name, engine, is_active, last_checked_at, last_check_ok, "
                        "last_check_error FROM connections ORDER BY name"
                    )
                )
            ).mappings()
        ]

    series = []
    for n in range(math.ceil(hours * 3600 / step_s)):
        row = by_bucket.get(n)
        series.append(
            Bucket(
                start=start + timedelta(seconds=n * step_s),
                calls=row["calls"] if row else 0,
                errors=row["errors"] if row else 0,
                p95_query_ms=round(row["p95"]) if row and row["p95"] is not None else None,
            )
        )

    calls = totals["calls"]
    return Health(
        checked_at=now,
        database=await _database(ctx),
        redis=await _redis(ctx),
        mcp_server=await _mcp_server(ctx),
        pool=_pool(ctx),
        window=Window(
            hours=hours,
            calls=calls,
            errors=totals["errors"],
            error_rate=totals["errors"] / calls if calls else None,
            query_calls=totals["query_calls"],
            p95_query_ms=round(totals["p95"]) if totals["p95"] is not None else None,
        ),
        series=series,
        connections=connections,
    )
