"""The health dashboard's API: component status, and error rate and latency from the audit log."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def empty_audit_log(api):
    """Health numbers are totals over the whole log, so each test starts from an empty one."""
    async with api.owner.begin() as db:
        await db.execute(text("DELETE FROM audit_log"))
    yield
    async with api.owner.begin() as db:
        await db.execute(text("DELETE FROM audit_log"))


async def log_call(api, *, minutes_ago, tool="run_query", ms=10, ok=True):
    async with api.owner.begin() as db:
        await db.execute(
            text(
                "INSERT INTO audit_log (occurred_at, caller_sub, tool_name, success, duration_ms) "
                "VALUES (:at, 'u-ana', :tool, :ok, :ms)"
            ),
            {
                "at": datetime.now(UTC) - timedelta(minutes=minutes_ago),
                "tool": tool,
                "ok": ok,
                "ms": ms,
            },
        )


async def get_health(api, **params):
    response = await api.client.get("/api/health", params=params, headers=api.admin)
    assert response.status_code == 200, response.text
    return response.json()


async def test_an_empty_log_says_so_rather_than_reporting_a_zero_error_rate(api):
    health = await get_health(api)
    assert health["window"]["calls"] == 0
    assert health["window"]["error_rate"] is None and health["window"]["p95_query_ms"] is None
    assert all(bucket["calls"] == 0 for bucket in health["series"])


async def test_error_rate_and_p95_come_from_the_audit_log(api):
    for ms in range(1, 21):  # 20 queries taking 1..20 ms: p95 is 19
        await log_call(api, minutes_ago=30, ms=ms)
    await log_call(api, minutes_ago=20, tool="list_tables", ms=900, ok=False)  # a failure
    await log_call(api, minutes_ago=10, ms=5, ok=False)  # a refused query
    await log_call(api, minutes_ago=60 * 48, ms=5, ok=False)  # too old to count

    window = (await get_health(api))["window"]
    assert (window["calls"], window["errors"]) == (22, 2)
    assert window["error_rate"] == pytest.approx(2 / 22)
    assert window["query_calls"] == 21
    # list_tables' 900 ms doesn't skew query latency, and the refused 5 ms query is included
    assert window["p95_query_ms"] == 19


async def test_the_window_can_be_widened(api):
    await log_call(api, minutes_ago=60 * 30)
    assert (await get_health(api, hours=24))["window"]["calls"] == 0
    assert (await get_health(api, hours=48))["window"]["calls"] == 1


async def test_the_time_series_accounts_for_every_call_in_order(api):
    await log_call(api, minutes_ago=5, ms=10)
    await log_call(api, minutes_ago=5, ms=30, ok=False)
    await log_call(api, minutes_ago=600, ms=50)
    health = await get_health(api)
    series = health["series"]
    assert sum(b["calls"] for b in series) == 3 and sum(b["errors"] for b in series) == 1
    starts = [b["start"] for b in series]
    assert starts == sorted(starts) and len(series) >= 48
    assert series[-1]["calls"] == 2  # the two recent ones share the newest bucket
    assert series[-1]["p95_query_ms"] is not None
    assert next(b for b in series if b["calls"] == 0)["p95_query_ms"] is None


async def test_it_reports_the_database_and_its_pool(api):
    health = await get_health(api)
    assert health["database"]["state"] == "ok" and health["database"]["latency_ms"] >= 0
    pool = health["pool"]
    assert pool["size"] == 10 and pool["in_use"] <= pool["size"]


async def test_redis_and_the_mcp_server_are_not_configured_unless_they_are(api):
    health = await get_health(api)
    assert health["redis"]["state"] == "not_configured"
    assert health["mcp_server"]["state"] == "not_configured"


async def test_redis_is_checked_when_configured(api, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(api.ctx.settings, "redis_url", SecretStr("redis://unused"))
    assert (await get_health(api))["redis"]["state"] == "ok"

    async def no_answer() -> bool:
        return False

    monkeypatch.setattr(api.cache, "ping", no_answer)
    down = (await get_health(api))["redis"]
    assert down["state"] == "down" and "without caching" in down["detail"]


@pytest.mark.parametrize(
    ("handler", "state"),
    [
        (lambda request: httpx.Response(200, json={"status": "ok"}), "ok"),
        (lambda request: httpx.Response(503), "down"),
        (lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused")), "down"),
    ],
)
async def test_the_mcp_server_is_probed_when_its_address_is_known(api, monkeypatch, handler, state):
    monkeypatch.setattr(api.ctx.settings, "mcp_health_url", "http://mcp.test/healthz")
    monkeypatch.setattr(api.ctx, "http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await get_health(api))["mcp_server"]["state"] == state


async def test_registered_connections_show_their_last_check(api):
    async with api.owner.begin() as db:
        await db.execute(
            text(
                "UPDATE connections SET last_checked_at = now(), last_check_ok = false, "
                "last_check_error = 'Connection failed: could not reach host x.' WHERE name = 'shop-sqlite'"
            )
        )
    try:
        connections = {c["name"]: c for c in (await get_health(api))["connections"]}
        assert connections["shop-sqlite"]["last_check_ok"] is False
        assert "could not reach" in connections["shop-sqlite"]["last_check_error"]
        assert connections["shop-pg"]["engine"] == "postgresql"
    finally:
        async with api.owner.begin() as db:
            await db.execute(
                text(
                    "UPDATE connections SET last_checked_at = NULL, last_check_ok = NULL, "
                    "last_check_error = NULL WHERE name = 'shop-sqlite'"
                )
            )


async def test_only_administrators_can_see_health(api):
    assert (await api.client.get("/api/health")).status_code == 401
    assert (await api.client.get("/api/health", headers=api.member)).status_code == 403
    too_wide = await api.client.get("/api/health", params={"hours": 500}, headers=api.admin)
    assert too_wide.status_code == 422
