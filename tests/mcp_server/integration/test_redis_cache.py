"""Caching against a real Redis, wired into the real services and databases."""

import asyncio
import secrets
import time
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from mcp_sql_server.cache.base import META, SCHEMA
from mcp_sql_server.cache.cached_adapter import CachedAdapter
from mcp_sql_server.cache.redis_cache import RedisCache, create_cache
from mcp_sql_server.config import Settings
from mcp_sql_server.container import build_services
from mcp_sql_server.errors import TableNotFound
from mcp_sql_server.models import Caller

REGISTERED = pytest.mark.usefixtures("registered")


@dataclass(frozen=True)
class RedisInfra:
    host: str
    port: int
    password: str

    @property
    def url(self) -> str:
        return f"redis://:{self.password}@{self.host}:{self.port}/0"


@pytest.fixture(scope="session")
def redis_server() -> Iterator[RedisInfra]:
    try:
        import docker
        from testcontainers.core.container import DockerContainer

        docker.from_env().ping()
    except Exception as exc:
        pytest.skip(f"Docker is not available: {exc}")

    password = secrets.token_hex(8)
    container = (
        DockerContainer("redis:7-alpine")
        .with_command(f"redis-server --requirepass {password}")
        .with_exposed_ports(6379)
    )
    with container:
        infra = RedisInfra(
            container.get_container_host_ip(), int(container.get_exposed_port(6379)), password
        )

        async def wait() -> None:
            client = Redis.from_url(infra.url)
            for _ in range(60):
                try:
                    if await client.ping():
                        break
                except Exception:
                    await asyncio.sleep(0.5)
            await client.aclose()

        asyncio.run(wait())
        yield infra


@pytest.fixture
async def cache(redis_server):
    c = RedisCache.from_url(redis_server.url)
    yield c
    await c.close()


# --- the Redis cache itself -----------------------------------------------------------------------


async def test_values_round_trip_and_expire(cache):
    key = f"t-{uuid4().hex}"
    await cache.set(key, "hello", ttl_s=1)
    assert await cache.get(key) == "hello"
    await asyncio.sleep(1.3)
    assert await cache.get(key) is None


async def test_versions_start_at_zero_and_bump_atomically(cache):
    family = f"family-{uuid4().hex}"
    assert await cache.version(family) == 0
    await asyncio.gather(*(cache.bump(family) for _ in range(10)))
    assert await cache.version(family) == 10


async def test_ping(cache):
    assert await cache.ping() is True


async def test_an_unreachable_redis_answers_quickly_and_quietly():
    dead = RedisCache.from_url("redis://:x@127.0.0.1:1/0")  # nothing listens on port 1
    started = time.monotonic()
    assert await dead.get("k") is None
    await dead.set("k", "v", 5)
    assert await dead.version(META) is None
    assert await dead.ping() is False
    assert (
        time.monotonic() - started < 4
    )  # timeouts are short, so a Redis outage isn't an app outage
    await dead.close()


def test_create_cache_uses_redis_only_when_configured(redis_server):
    off = Settings(
        _env_file=None, app_meta_url="postgresql+asyncpg://x", connection_secret_keys="k"
    )
    on = Settings(
        _env_file=None,
        app_meta_url="postgresql+asyncpg://x",
        connection_secret_keys="k",
        redis_url=redis_server.url,
    )
    assert type(create_cache(off)).__name__ == "NullCache"
    assert type(create_cache(on)).__name__ == "RedisCache"


# --- the whole thing, with caching on ---------------------------------------------------------------


@pytest.fixture
async def cached_services(postgres, fernet_key, redis_server):
    settings = Settings(
        _env_file=None,
        app_meta_url=postgres.url("mcp_app", "app_meta"),
        connection_secret_keys=fernet_key,
        redis_url=redis_server.url,
        default_query_timeout_s=2,
    )
    services = build_services(settings)
    yield services
    await services.close()


def count_reflection(services, name: str) -> dict[str, int]:
    """Wrap the SQLAlchemy adapter behind a connection so we can count real catalog reads."""
    adapter = services.registry._adapters
    (_, cached_adapter) = next(a for a in adapter.values() if a[1].dialect_name)
    assert isinstance(cached_adapter, CachedAdapter)
    inner = cached_adapter._inner
    counts = {"list_tables": 0, "describe_table": 0}

    real_list, real_describe = inner.list_tables, inner.describe_table

    async def list_tables():
        counts["list_tables"] += 1
        return await real_list()

    async def describe_table(table):
        counts["describe_table"] += 1
        return await real_describe(table)

    inner.list_tables, inner.describe_table = list_tables, describe_table  # type: ignore[method-assign]
    return counts


@REGISTERED
@pytest.mark.parametrize("conn", ["shop-pg", "shop-sqlite"])
async def test_table_structure_is_read_from_the_database_once_then_from_redis(
    cached_services, conn
):
    who = Caller(sub=f"c-{uuid4().hex[:8]}", roles=frozenset({"analyst"}))
    await cached_services.schema.list_tables(who, conn)  # opens the adapter
    counts = count_reflection(cached_services, conn)
    await cache_flush(cached_services)  # start cold, on this very adapter

    first = await cached_services.schema.list_tables(who, conn)
    detail1 = await cached_services.schema.describe_table(who, conn, "orders")
    reads_after_first = dict(counts)
    for _ in range(3):
        assert await cached_services.schema.list_tables(who, conn) == first
        assert await cached_services.schema.describe_table(who, conn, "orders") == detail1
    assert counts == reads_after_first  # nothing further reached the database's catalog


async def cache_flush(services) -> None:
    """Make every cached schema entry unreachable, as the admin GUI's 'refresh' will."""
    await services.cache.bump(SCHEMA)
    await services.cache.bump(META)


@REGISTERED
async def test_relationships_get_cheap_once_warm(cached_services):
    who = Caller(sub=f"c-{uuid4().hex[:8]}", roles=frozenset({"analyst"}))
    await cached_services.schema.list_tables(who, "shop-pg")
    counts = count_reflection(cached_services, "shop-pg")
    await cache_flush(cached_services)

    await cached_services.schema.get_relationships(who, "shop-pg", "orders")
    cold = dict(counts)
    await cached_services.schema.get_relationships(who, "shop-pg", "orders")
    assert counts == cold


@REGISTERED
async def test_callers_with_different_access_never_share_cached_results(cached_services, stack):
    """A warm cache from a wide-access caller must not widen a narrow caller's view."""
    wide = Caller(sub=f"w-{uuid4().hex[:8]}", roles=frozenset({"analyst"}))
    narrow_role = f"narrow-{uuid4().hex[:6]}"
    narrow = Caller(sub=f"n-{uuid4().hex[:8]}", roles=frozenset({narrow_role}))

    async with stack.admin.begin() as db:
        pg = (
            await db.execute(text("SELECT id FROM connections WHERE name = 'shop-pg'"))
        ).scalar_one()
        await db.execute(
            text("INSERT INTO connection_access VALUES (:c, 'role', :r)"),
            {"c": pg, "r": narrow_role},
        )
        await db.execute(
            text(
                "INSERT INTO table_permissions (connection_id, subject_type, subject_id, table_name) "
                "VALUES (:c, 'role', :r, 'customers')"
            ),
            {"c": pg, "r": narrow_role},
        )
        for tool in ("list_tables", "describe_table"):
            await db.execute(
                text(
                    "INSERT INTO tool_permissions (subject_type, subject_id, tool_name) "
                    "VALUES ('role', :r, :t)"
                ),
                {"r": narrow_role, "t": tool},
            )

    wide_view = [t.name for t in await cached_services.schema.list_tables(wide, "shop-pg")]
    narrow_view = [t.name for t in await cached_services.schema.list_tables(narrow, "shop-pg")]
    wide_again = [t.name for t in await cached_services.schema.list_tables(wide, "shop-pg")]

    assert "orders" in wide_view and wide_view == wide_again
    assert narrow_view == ["customers"]
    with pytest.raises(TableNotFound):
        await cached_services.schema.describe_table(narrow, "shop-pg", "orders")


@REGISTERED
async def test_revoking_access_takes_effect_the_moment_the_cache_is_invalidated(
    cached_services, stack
):
    role = f"temp-{uuid4().hex[:6]}"
    who = Caller(sub=f"t-{uuid4().hex[:8]}", roles=frozenset({role}))
    async with stack.admin.begin() as db:
        pg = (
            await db.execute(text("SELECT id FROM connections WHERE name = 'shop-pg'"))
        ).scalar_one()
        await db.execute(
            text("INSERT INTO connection_access VALUES (:c, 'role', :r)"), {"c": pg, "r": role}
        )
        await db.execute(
            text(
                "INSERT INTO table_permissions (connection_id, subject_type, subject_id, table_name) "
                "VALUES (:c, 'role', :r, 'orders')"
            ),
            {"c": pg, "r": role},
        )
        await db.execute(
            text(
                "INSERT INTO tool_permissions (subject_type, subject_id, tool_name) VALUES ('role', :r, 'run_query')"
            ),
            {"r": role},
        )

    ok = await cached_services.query.run_query(who, "shop-pg", "SELECT count(*) FROM orders")
    assert ok.rows == [[70]]  # and the permission lookups are now cached

    async with stack.admin.begin() as db:
        await db.execute(text("DELETE FROM table_permissions WHERE subject_id = :r"), {"r": role})

    still = await cached_services.query.run_query(who, "shop-pg", "SELECT count(*) FROM orders")
    assert still.rows == [[70]]  # stale: the revoke hasn't been announced yet

    await cached_services.cache.bump(META)  # the admin GUI does this after every change
    with pytest.raises(TableNotFound):
        await cached_services.query.run_query(who, "shop-pg", "SELECT count(*) FROM orders")


@REGISTERED
async def test_query_results_are_never_cached(cached_services):
    who = Caller(sub=f"q-{uuid4().hex[:8]}", roles=frozenset({"analyst"}))
    keys_before = await raw_keys(cached_services)
    for _ in range(2):
        await cached_services.query.run_query(who, "shop-pg", "SELECT count(*) FROM orders")
    new_keys = await raw_keys(cached_services) - keys_before
    assert not any("select" in key.lower() or "count" in key.lower() for key in new_keys)


async def raw_keys(services) -> set[str]:
    client = services.cache._redis
    return {key async for key in client.scan_iter("mcpsql:*")}
