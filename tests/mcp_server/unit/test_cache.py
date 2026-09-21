"""Caching: that it saves work, and that it never leaks, goes stale, or breaks requests."""

import pytest
from conftest import ANALYST, FakeAdapter, FakeMetaStore
from fake_cache import BrokenCache, MemoryCache
from pydantic import TypeAdapter
from redis.exceptions import ConnectionError as RedisConnectionError

from mcp_sql_server.cache.base import META, SCHEMA, NullCache
from mcp_sql_server.cache.cached_adapter import CachedAdapter
from mcp_sql_server.cache.cached_meta_store import CachedMetaStore
from mcp_sql_server.cache.helpers import cached_json, subjects_fingerprint
from mcp_sql_server.cache.redis_cache import RedisCache
from mcp_sql_server.errors import TableNotFound
from mcp_sql_server.models import Caller

STRINGS = TypeAdapter(list[str])


# --- the helper that everything uses ----------------------------------------------------------


class Counter:
    def __init__(self, value):
        self.value, self.calls = value, 0

    async def __call__(self):
        self.calls += 1
        return self.value


async def test_a_value_is_loaded_once_and_then_served_from_the_cache():
    cache, load = MemoryCache(), Counter(["a", "b"])
    for _ in range(3):
        assert await cached_json(cache, META, "k", 60, STRINGS, load) == ["a", "b"]
    assert load.calls == 1


async def test_entries_expire():
    cache, load = MemoryCache(), Counter(["a"])
    await cached_json(cache, META, "k", 60, STRINGS, load)
    cache.now = 61
    await cached_json(cache, META, "k", 60, STRINGS, load)
    assert load.calls == 2


async def test_bumping_the_version_makes_every_old_entry_unreachable_at_once():
    cache, load = MemoryCache(), Counter(["a"])
    await cached_json(cache, META, "k1", 60, STRINGS, load)
    await cached_json(cache, META, "k2", 60, STRINGS, load)
    await cache.bump(META)
    await cached_json(cache, META, "k1", 60, STRINGS, load)
    await cached_json(cache, META, "k2", 60, STRINGS, load)
    assert load.calls == 4


async def test_families_are_invalidated_independently():
    cache, meta_load, schema_load = MemoryCache(), Counter(["m"]), Counter(["s"])
    await cached_json(cache, META, "k", 60, STRINGS, meta_load)
    await cached_json(cache, SCHEMA, "k", 60, STRINGS, schema_load)
    await cache.bump(META)
    await cached_json(cache, META, "k", 60, STRINGS, meta_load)
    await cached_json(cache, SCHEMA, "k", 60, STRINGS, schema_load)
    assert (meta_load.calls, schema_load.calls) == (2, 1)


async def test_if_the_version_is_unknown_the_cache_is_skipped_completely():
    cache, load = BrokenCache(), Counter(["a"])
    for _ in range(3):
        assert await cached_json(cache, META, "k", 60, STRINGS, load) == ["a"]
    assert load.calls == 3


async def test_a_corrupt_entry_is_treated_as_a_miss_not_an_error():
    cache, load = MemoryCache(), Counter(["fresh"])
    await cache.set(f"{META}:0:k", "{not json", 60)
    assert await cached_json(cache, META, "k", 60, STRINGS, load) == ["fresh"]


async def test_null_cache_just_loads_every_time():
    load = Counter(["a"])
    for _ in range(2):
        await cached_json(NullCache(), META, "k", 60, STRINGS, load)
    assert load.calls == 2


# --- cache keys for callers -----------------------------------------------------------------------


def test_the_fingerprint_ignores_order_but_not_content():
    a = subjects_fingerprint([("user", "u1"), ("role", "analyst"), ("role", "viewer")])
    b = subjects_fingerprint([("role", "viewer"), ("user", "u1"), ("role", "analyst")])
    assert a == b
    assert a != subjects_fingerprint([("user", "u1"), ("role", "analyst")])


def test_a_user_and_a_role_with_the_same_name_never_share_a_key():
    assert subjects_fingerprint([("user", "analyst")]) != subjects_fingerprint(
        [("role", "analyst")]
    )


# --- permission and description cache -----------------------------------------------------------------


class CountingStore(FakeMetaStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def list_connections(self, subjects):
        self._count("list_connections")
        return await super().list_connections(subjects)

    async def get_connection(self, name, subjects):
        self._count("get_connection")
        return await super().get_connection(name, subjects)

    async def allowed_tables(self, connection_id, subjects):
        self._count("allowed_tables")
        return await super().allowed_tables(connection_id, subjects)

    async def allowed_tools(self, subjects):
        self._count("allowed_tools")
        return await super().allowed_tools(subjects)

    async def descriptions(self, connection_id):
        self._count("descriptions")
        return await super().descriptions(connection_id)


@pytest.fixture
def store(env):
    """The unit-test environment's data, behind a counting store and a cache."""
    counting = CountingStore()
    counting.connections = env.store.connections
    counting.connection_access = env.store.connection_access
    counting.table_grants = env.store.table_grants
    counting.tool_grants = env.store.tool_grants
    counting.curated = {
        env.connection_id: {("orders", None): "Orders", ("orders", "status"): "Stage"}
    }
    cache = MemoryCache()
    return counting, CachedMetaStore(counting, cache), cache


async def test_permission_lookups_hit_the_database_once_per_caller(env, store):
    inner, cached, _ = store
    for _ in range(4):
        assert await cached.allowed_tables(env.connection_id, ANALYST.subjects()) == {
            "customers",
            "orders",
            "categories",
        }
        assert "run_query" in await cached.allowed_tools(ANALYST.subjects())
    assert inner.calls == {"allowed_tables": 1, "allowed_tools": 1}


async def test_two_callers_with_different_access_never_see_each_others_cached_answers(env, store):
    _, cached, _ = store
    env.store.table_grants.add((env.connection_id, "user", "narrow", "customers"))
    narrow = Caller(sub="narrow")

    assert "orders" in await cached.allowed_tables(env.connection_id, ANALYST.subjects())
    assert await cached.allowed_tables(env.connection_id, narrow.subjects()) == {"customers"}
    # and the reverse order, with the cache now warm for both
    assert await cached.allowed_tables(env.connection_id, narrow.subjects()) == {"customers"}
    assert "orders" in await cached.allowed_tables(env.connection_id, ANALYST.subjects())


async def test_the_same_subjects_in_a_different_order_share_an_entry(env, store):
    inner, cached, _ = store
    a = Caller(sub="u", roles=frozenset({"analyst", "viewer"}))
    await cached.allowed_tools(a.subjects())
    await cached.allowed_tools(tuple(reversed(a.subjects())))
    assert inner.calls["allowed_tools"] == 1


async def test_a_revoked_grant_is_still_served_until_the_cache_is_invalidated(env, store):
    """This is why the admin GUI bumps the version after every permission change."""
    _, cached, cache = store
    assert "orders" in await cached.allowed_tables(env.connection_id, ANALYST.subjects())

    env.store.table_grants.discard((env.connection_id, "role", "analyst", "orders"))
    assert "orders" in await cached.allowed_tables(env.connection_id, ANALYST.subjects())  # stale

    await cache.bump(META)  # what the GUI does
    assert "orders" not in await cached.allowed_tables(env.connection_id, ANALYST.subjects())


async def test_a_grant_that_expires_by_ttl_alone_is_eventually_dropped(env, store):
    _, cached, cache = store
    await cached.allowed_tables(env.connection_id, ANALYST.subjects())
    env.store.table_grants.discard((env.connection_id, "role", "analyst", "orders"))
    cache.now = 61  # permission entries live 60 seconds
    assert "orders" not in await cached.allowed_tables(env.connection_id, ANALYST.subjects())


async def test_connection_records_survive_the_round_trip_with_their_types(env, store):
    _, cached, _ = store
    first = await cached.list_connections(ANALYST.subjects())
    again = await cached.list_connections(ANALYST.subjects())
    assert first == again
    assert again[0].id == env.connection_id
    assert again[0].updated_at == env.store.connections["shop"].updated_at


async def test_the_connection_secret_is_never_copied_into_the_cache(env, store):
    inner, cached, cache = store
    await cached.get_connection("shop", ANALYST.subjects())
    await cached.get_connection("shop", ANALYST.subjects())
    assert inner.calls["get_connection"] == 2  # always read from the database
    assert all("secret" not in key for key in cache.data)


async def test_descriptions_keep_their_table_and_column_keys(env, store):
    inner, cached, _ = store
    for _ in range(2):
        found = await cached.descriptions(env.connection_id)
        assert found == {("orders", None): "Orders", ("orders", "status"): "Stage"}
    assert inner.calls["descriptions"] == 1


async def test_audit_writes_are_never_cached_or_dropped(env, store):
    inner, cached, _ = store
    from mcp_sql_server.models import AuditEntry

    entry = AuditEntry("u", None, "run_query", "shop", {}, 1, True)
    await cached.write_audit(entry)
    await cached.write_audit(entry)
    assert len(inner.audit) == 2


async def test_a_dead_cache_costs_speed_but_never_correctness(env):
    counting = CountingStore()
    counting.connections = env.store.connections
    counting.connection_access = env.store.connection_access
    counting.table_grants = env.store.table_grants
    cached = CachedMetaStore(counting, BrokenCache())
    for _ in range(3):
        assert "orders" in await cached.allowed_tables(env.connection_id, ANALYST.subjects())
    assert counting.calls["allowed_tables"] == 3


# --- schema cache -----------------------------------------------------------------------------


@pytest.fixture
def adapters():
    inner, cache = FakeAdapter(), MemoryCache()
    return inner, CachedAdapter(inner, cache, "conn1:v1"), cache


def calls_of(adapter: FakeAdapter, name: str) -> int:
    return sum(1 for c in adapter.calls if c[0] == name)


async def test_the_table_list_is_read_from_the_database_once(adapters):
    inner, cached, _ = adapters
    first = await cached.list_tables()
    assert await cached.list_tables() == first
    assert calls_of(inner, "list_tables") == 1


async def test_each_tables_structure_is_cached_and_survives_the_round_trip(adapters):
    inner, cached, _ = adapters
    first = await cached.describe_table("orders")
    again = await cached.describe_table("orders")
    assert first == again == inner.tables["orders"]  # tuples and dataclasses come back intact
    assert calls_of(inner, "describe_table") == 1

    await cached.describe_table("ORDERS")  # same table, different spelling
    assert calls_of(inner, "describe_table") == 1
    await cached.describe_table("customers")
    assert calls_of(inner, "describe_table") == 2


async def test_errors_are_not_cached(adapters):
    inner, cached, _ = adapters
    for _ in range(2):
        with pytest.raises(TableNotFound):
            await cached.describe_table("nope")
    assert calls_of(inner, "describe_table") == 2


async def test_queries_are_never_cached(adapters):
    inner, cached, _ = adapters
    for _ in range(3):
        await cached.execute("SELECT 1", max_rows=5, timeout_s=1)
        await cached.explain("SELECT 1", timeout_s=1)
        await cached.sample_rows("orders", 2, timeout_s=1)
    assert [calls_of(inner, n) for n in ("execute", "explain", "sample_rows")] == [3, 3, 3]


async def test_editing_a_connection_starts_a_fresh_set_of_entries(adapters):
    inner, _, cache = adapters
    await CachedAdapter(inner, cache, "conn1:v1").list_tables()
    await CachedAdapter(inner, cache, "conn1:v2").list_tables()  # updated_at changed
    await CachedAdapter(inner, cache, "conn2:v1").list_tables()  # a different connection
    assert calls_of(inner, "list_tables") == 3


async def test_bumping_the_schema_version_forces_a_re_read(adapters):
    inner, cached, cache = adapters
    await cached.list_tables()
    await cache.bump(SCHEMA)
    await cached.list_tables()
    assert calls_of(inner, "list_tables") == 2


async def test_the_cached_structure_contains_nothing_about_who_asked(adapters):
    _, cached, cache = adapters
    await cached.describe_table("orders")
    for key, (value, _) in cache.data.items():
        assert "user-1" not in key + value and "analyst" not in key + value


def test_the_adapter_reports_the_wrapped_adapters_dialect(adapters):
    inner, cached, _ = adapters
    assert (cached.dialect_name, cached.default_schema) == ("postgresql", "public")


# --- redis failures ------------------------------------------------------------------------------


class DeadRedis:
    async def get(self, *_a, **_k):
        raise RedisConnectionError("connection refused")

    set = incr = ping = get

    async def aclose(self):
        pass


async def test_a_redis_outage_is_absorbed(caplog):
    cache = RedisCache(DeadRedis())  # type: ignore[arg-type]
    assert await cache.get("k") is None
    await cache.set("k", "v", 10)  # doesn't raise
    assert await cache.version(META) is None  # "can't tell", so callers skip the cache
    assert await cache.ping() is False
    with caplog.at_level("ERROR"):
        await cache.bump(META)  # doesn't raise, but is logged loudly
    assert "stale entries may be served" in caplog.text


class CountingDeadRedis(DeadRedis):
    def __init__(self) -> None:
        self.calls = 0

    async def get(self, *_a, **_k):
        self.calls += 1
        raise RedisConnectionError("connection refused")

    set = incr = ping = get


async def test_after_one_failure_redis_is_left_alone_for_a_while(monkeypatch):
    """Otherwise every request would wait through several timeouts, and a cache outage would
    become a whole-service slowdown."""
    from mcp_sql_server.cache import redis_cache

    now = [1000.0]
    monkeypatch.setattr(redis_cache.time, "monotonic", lambda: now[0])
    client = CountingDeadRedis()
    cache = RedisCache(client)  # type: ignore[arg-type]

    assert await cache.get("k") is None  # notices the failure
    assert client.calls == 1
    for _ in range(5):
        assert await cache.get("k") is None
        assert await cache.version(META) is None
        await cache.set("k", "v", 5)
    assert client.calls == 1  # nothing further touched Redis

    now[0] += redis_cache.COOLDOWN_S + 1  # the cooldown passes
    await cache.get("k")
    assert client.calls == 2  # so it tries again


async def test_invalidation_is_still_attempted_during_a_cooldown(monkeypatch):
    from mcp_sql_server.cache import redis_cache

    now = [1000.0]
    monkeypatch.setattr(redis_cache.time, "monotonic", lambda: now[0])
    client = CountingDeadRedis()
    cache = RedisCache(client)  # type: ignore[arg-type]
    await cache.get("k")  # trips the breaker
    await cache.bump(META)
    assert client.calls == 2
