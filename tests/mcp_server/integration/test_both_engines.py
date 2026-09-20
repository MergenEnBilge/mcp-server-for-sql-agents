"""The full service layer against real databases, once per engine.

Every test here runs twice, against `shop-pg` (Postgres) and `shop-sqlite` (SQLite),
which hold identical data. That's the point: the same calls must behave the same on
both. Where an engine legitimately differs (query plans), the test says so.

Sample data facts: 70 orders, 32 reviews (the last two are prompt-injection samples),
and the `analyst` role can see every table except `payments`.
"""

import hashlib
from pathlib import Path

import pytest

from mcp_sql_server.errors import QueryFailed, QueryRejected, QueryTimeout, TableNotFound

VISIBLE_TABLES = ["categories", "customers", "order_items", "orders", "products", "reviews"]


@pytest.fixture(params=["shop-pg", "shop-sqlite"])
def conn(request) -> str:
    return request.param


# --- discovery ---------------------------------------------------------------------------------


async def test_list_connections_shows_both_databases_and_their_engines(stack):
    connections = {c.name: c.engine for c in await stack.schema.list_connections(stack.caller)}
    assert connections == {"shop-pg": "postgresql", "shop-sqlite": "sqlite"}


async def test_list_tables_shows_the_visible_tables_with_descriptions(stack, conn):
    tables = {t.name: t for t in await stack.schema.list_tables(stack.caller, conn)}
    assert sorted(tables) == VISIBLE_TABLES  # payments exists in the database but is hidden
    assert tables["orders"].kind == "table"
    assert "one row per checkout" in tables["orders"].description.lower()


async def test_describe_table_gives_columns_keys_and_curated_descriptions(stack, conn):
    detail = await stack.schema.describe_table(stack.caller, conn, "orders")

    assert [c.name for c in detail.columns] == [
        "id",
        "customer_id",
        "status",
        "ordered_at",
        "shipping_country",
    ]
    by_name = {c.name: c for c in detail.columns}
    assert by_name["id"].primary_key and not by_name["id"].nullable
    assert not by_name["status"].primary_key
    assert all(c.type for c in detail.columns)
    assert "pending, paid, shipped" in by_name["status"].description
    assert [(fk.columns, fk.to_table, fk.to_columns) for fk in detail.foreign_keys] == [
        (["customer_id"], "customers", ["id"])
    ]


async def test_describe_table_ignores_the_case_of_the_name(stack, conn):
    assert (await stack.schema.describe_table(stack.caller, conn, "ORDERS")).name == "orders"


async def test_relationships_leave_out_the_hidden_payments_table(stack, conn):
    rel = await stack.schema.get_relationships(stack.caller, conn, "orders")
    assert [fk.to_table for fk in rel.references] == ["customers"]
    assert sorted(fk.from_table for fk in rel.referenced_by) == ["order_items"]  # not payments


async def test_a_self_referencing_key_shows_up_in_both_directions(stack, conn):
    rel = await stack.schema.get_relationships(stack.caller, conn, "categories")
    assert [(fk.from_table, fk.to_table) for fk in rel.references] == [("categories", "categories")]
    assert [(fk.from_table, fk.to_table) for fk in rel.referenced_by] == [
        ("categories", "categories"),
        ("products", "categories"),
    ]


async def test_search_finds_tables_by_meaning_and_by_typo_but_never_hidden_ones(stack, conn):
    checkout = await stack.schema.search_schema(stack.caller, conn, "checkout")
    assert checkout and checkout[0].table == "orders"

    typo = await stack.schema.search_schema(stack.caller, conn, "custmers")
    assert typo and typo[0].table == "customers"

    stars = await stack.schema.search_schema(stack.caller, conn, "stars")
    assert any(h.table == "reviews" and h.column == "rating" for h in stars)

    # "payment" appears in the descriptions of visible tables, and of the hidden payments table itself
    payment = await stack.schema.search_schema(stack.caller, conn, "payment")
    assert payment
    assert "payments" not in {h.table for h in payment}


# --- running queries ---------------------------------------------------------------------------


async def test_an_aggregate_query_returns_the_same_answer_on_both_engines(stack, conn):
    result = await stack.query.run_query(
        stack.caller,
        conn,
        "SELECT status, count(*) AS n FROM orders GROUP BY status ORDER BY status",
    )
    assert result.columns == ["status", "n"]
    assert sum(row[1] for row in result.rows) == 70
    assert [row[0] for row in result.rows] == sorted(row[0] for row in result.rows)
    assert result.truncated is False


async def test_joins_ctes_and_subqueries_work(stack, conn):
    sql = """
        WITH totals AS (
            SELECT order_id, SUM(quantity * unit_price) AS total FROM order_items GROUP BY order_id
        )
        SELECT c.country, COUNT(*) AS orders
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        JOIN totals t ON t.order_id = o.id
        WHERE o.id IN (SELECT id FROM orders WHERE status <> 'cancelled')
        GROUP BY c.country
    """
    result = await stack.query.run_query(stack.caller, conn, sql)
    assert result.row_count > 0
    assert sum(row[1] for row in result.rows) <= 70


async def test_the_row_cap_is_enforced_and_reported(stack, conn):
    result = await stack.query.run_query(
        stack.caller, conn, "SELECT id FROM orders ORDER BY id", row_limit=5
    )
    assert [row[0] for row in result.rows] == [1, 2, 3, 4, 5]
    assert result.row_count == 5
    assert result.truncated is True

    everything = await stack.query.run_query(
        stack.caller, conn, "SELECT id FROM orders ORDER BY id", row_limit=100
    )
    assert everything.row_count == 70
    assert everything.truncated is False


async def test_decimal_prices_come_back_as_the_same_number_on_both_engines(stack, conn):
    result = await stack.query.run_query(
        stack.caller, conn, "SELECT unit_price FROM products WHERE id = 2"
    )
    assert result.rows == [[59.9]]


async def test_sample_rows(stack, conn):
    result = await stack.query.get_sample_rows(stack.caller, conn, "products", n=3)
    assert "sku" in result.columns
    assert result.row_count == 3
    assert result.truncated is False


async def test_explain_returns_a_plan_without_running_the_query(stack, conn):
    plan = await stack.query.explain_query(
        stack.caller, conn, "SELECT * FROM orders WHERE customer_id = 3"
    )
    assert "orders" in plan.plan.lower()
    if conn == "shop-pg":
        assert plan.estimated_cost is not None and plan.estimated_rows is not None
    else:  # SQLite has a plan but no cost estimate
        assert plan.estimated_cost is None


async def test_queries_that_run_too_long_are_stopped_and_the_connection_stays_usable(stack, conn):
    if conn == "shop-pg":
        endless = "SELECT count(*) FROM generate_series(1, 2000000000)"
    else:
        endless = (
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 900000000) "
            "SELECT count(*) FROM c"
        )
    with pytest.raises(QueryTimeout, match="1.5s"):
        await stack.query.run_query(stack.caller, conn, endless)

    after = await stack.query.run_query(stack.caller, conn, "SELECT count(*) FROM orders")
    assert after.rows == [[70]]


# --- what is refused --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM payments",
        "SELECT o.id FROM orders o JOIN payments p ON p.order_id = o.id",
        "SELECT id FROM orders WHERE id IN (SELECT order_id FROM payments)",
        "WITH p AS (SELECT * FROM payments) SELECT * FROM p",
        "SELECT * FROM (SELECT * FROM payments) x, (WITH orders AS (SELECT 1) SELECT * FROM orders) y",
    ],
)
async def test_the_hidden_table_cannot_be_reached_by_any_route(stack, conn, sql):
    with pytest.raises(TableNotFound):
        await stack.query.run_query(stack.caller, conn, sql)


async def test_catalog_tables_are_off_limits_because_they_would_list_hidden_tables(stack, conn):
    catalog = "information_schema.tables" if conn == "shop-pg" else "sqlite_master"
    with pytest.raises(TableNotFound):
        await stack.query.run_query(stack.caller, conn, f"SELECT * FROM {catalog}")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM reviews",
        "DROP TABLE orders",
        "SELECT 1; DELETE FROM reviews",
        "WITH d AS (DELETE FROM reviews RETURNING *) SELECT * FROM d",
        "SELECT * INTO copy_of_orders FROM orders",
    ],
)
async def test_write_attempts_are_rejected_by_the_service_layer(stack, conn, sql):
    with pytest.raises(QueryRejected):
        await stack.query.run_query(stack.caller, conn, sql)
    count = await stack.query.run_query(stack.caller, conn, "SELECT count(*) FROM reviews")
    assert count.rows == [[32]]


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM reviews",
        "UPDATE customers SET is_active = FALSE",
        "INSERT INTO categories (id, name) VALUES (99, 'x')",
        "DROP TABLE orders",
        "CREATE TABLE sneaky (a INTEGER)",
    ],
)
async def test_the_database_itself_refuses_writes_even_if_the_service_layer_were_bypassed(
    stack, conn, statement, sqlite_file: Path
):
    """The second lock. We skip QueryService entirely and hand the SQL straight to the adapter."""
    record = await stack.store.get_connection(conn, stack.caller.subjects())
    assert record is not None
    adapter = await stack.registry.adapter_for(record)
    before = hashlib.sha256(sqlite_file.read_bytes()).hexdigest()

    with pytest.raises(QueryFailed):
        await adapter.execute(statement, max_rows=10, timeout_s=5)

    survivors = await adapter.execute("SELECT count(*) FROM reviews", max_rows=1, timeout_s=5)
    assert survivors.rows == [(32,)]
    assert hashlib.sha256(sqlite_file.read_bytes()).hexdigest() == before  # file bytes untouched


async def test_postgres_cannot_be_switched_back_to_read_write(stack):
    record = await stack.store.get_connection("shop-pg", stack.caller.subjects())
    assert record is not None
    adapter = await stack.registry.adapter_for(record)
    with pytest.raises(QueryFailed):
        await adapter.execute("SET TRANSACTION READ WRITE", max_rows=1, timeout_s=5)


# --- what comes back is untrusted ------------------------------------------------------------


async def test_review_text_that_tries_to_instruct_the_model_is_withheld(stack, conn):
    result = await stack.query.run_query(
        stack.caller, conn, "SELECT id, body FROM reviews WHERE id IN (1, 31, 32) ORDER BY id"
    )
    rows = {row[0]: row[1] for row in result.rows}

    assert "withheld" not in rows[1]  # an ordinary review is untouched
    assert "ignore all previous instructions" not in str(result.rows).lower()
    assert "DROP TABLE" not in str(result.rows)
    assert "withheld" in rows[31] and "withheld" in rows[32]
    assert [(f.location, f.rule) for f in result.security_flags] == [
        ("row 2, column body", "ignore_instructions"),
        ("row 3, column body", "tool_call_markup"),
    ]


# --- the audit trail ---------------------------------------------------------------------------


async def test_every_call_leaves_an_audit_row_including_refused_ones(stack, conn):
    from sqlalchemy import text

    await stack.query.run_query(stack.caller, conn, "SELECT id FROM orders", row_limit=3)
    with pytest.raises(TableNotFound):
        await stack.query.run_query(stack.caller, conn, "SELECT * FROM payments")
    await stack.schema.list_tables(stack.caller, conn)

    async with stack.admin.connect() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT tool_name, connection_name, tables, arguments, success, "
                        "error_message, row_count, duration_ms, caller_name "
                        "FROM audit_log WHERE caller_sub = :sub ORDER BY id"
                    ),
                    {"sub": stack.caller.sub},
                )
            )
            .mappings()
            .all()
        )

    assert [(r["tool_name"], r["success"]) for r in rows] == [
        ("run_query", True),
        ("run_query", False),
        ("list_tables", True),
    ]
    ok, denied, listing = rows
    assert ok["connection_name"] == conn and ok["caller_name"] == "Integration Test"
    assert ok["tables"] == ["orders"]
    assert ok["arguments"] == {
        "connection_name": conn,
        "sql": "SELECT id FROM orders",
        "row_limit": 3,
    }
    assert ok["row_count"] == 3 and ok["duration_ms"] >= 0
    assert denied["tables"] == ["payments"]  # what they reached for is on record
    assert "not available" in denied["error_message"]
    assert listing["tables"] == VISIBLE_TABLES
