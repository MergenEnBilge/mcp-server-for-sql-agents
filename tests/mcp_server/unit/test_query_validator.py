"""The SQL validator is a security boundary, so these tests read like a list of attacks."""

import pytest

from mcp_sql_server.errors import QueryRejected
from mcp_sql_server.services.query_validator import QueryValidator

validator = QueryValidator(max_length=2000)


def check(sql: str, dialect: str = "postgresql", default_schema: str | None = "public"):
    return validator.validate(sql, dialect=dialect, default_schema=default_schema)


# --- queries that must be accepted -------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "select o.id, c.email from orders o join customers c on c.id = o.customer_id",
        "SELECT * FROM orders;",
        "SELECT * FROM orders ;  ",
        "WITH recent AS (SELECT * FROM orders WHERE id > 5) SELECT * FROM recent",
        "SELECT id FROM orders UNION SELECT id FROM customers",
        "(SELECT id FROM orders) UNION (SELECT id FROM customers)",
        "SELECT * FROM customers WHERE id IN (SELECT customer_id FROM orders)",
        "SELECT 1 -- just a comment",
        "SELECT /* inline comment */ 1",
        # words that look dangerous but are data or part of a longer identifier
        "SELECT 'DROP TABLE orders; DELETE FROM x' AS harmless",
        'SELECT "update", created_at, updated_at, delete_reason FROM orders',
        "SELECT replace(email, '@', ' at ') FROM customers",
        "SELECT 'it''s fine; really' AS s",
        "SELECT status, count(*) FROM orders GROUP BY status HAVING count(*) > 1 ORDER BY 2 DESC",
        "SELECT '12:30'::text, ordered_at::date FROM orders",
    ],
)
def test_accepts_plain_reads(sql):
    assert check(sql).sql.startswith(("SELECT", "select", "WITH", "("))


def test_strips_one_trailing_semicolon():
    assert check("SELECT 1 FROM orders ;  ").sql == "SELECT 1 FROM orders"


def test_accepts_engine_specific_syntax_for_that_engine():
    assert check("SELECT TOP 5 * FROM orders ORDER BY id", dialect="mssql").tables == {"orders"}
    assert check("SELECT * FROM `orders` LIMIT 5", dialect="mysql").tables == {"orders"}
    assert check("SELECT [id] FROM [orders]", dialect="mssql").tables == {"orders"}
    assert check(
        "SELECT * FROM orders LIMIT 5", dialect="sqlite", default_schema="main"
    ).tables == {"orders"}


# --- queries that must be rejected --------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE orders",
        "DELETE FROM orders",
        "UPDATE orders SET status = 'paid'",
        "INSERT INTO categories (id, name) VALUES (99, 'x')",
        "ALTER TABLE orders ADD COLUMN x int",
        "TRUNCATE orders",
        "GRANT ALL ON orders TO public",
        "CREATE TABLE t (a int)",
        "MERGE INTO orders USING x ON true WHEN MATCHED THEN DELETE",
        "SELECT * INTO copy_of_orders FROM orders",
        "COPY orders TO '/tmp/x'",
        "CALL do_something()",
        "EXEC xp_cmdshell 'dir'",
        "PRAGMA writable_schema = 1",
        "EXPLAIN ANALYZE DELETE FROM orders",
        "SHOW TABLES",
        "VALUES (1), (2)",
        "SET default_transaction_read_only = off",
    ],
)
def test_rejects_anything_that_is_not_a_plain_read(sql):
    with pytest.raises(QueryRejected):
        check(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE orders",
        "SELECT 1; SELECT 2",
        "SELECT 1;; DROP TABLE orders",
        "SELECT * FROM orders; -- done\nDELETE FROM orders",
    ],
)
def test_rejects_multiple_statements(sql):
    with pytest.raises(QueryRejected):
        check(sql)


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        ("WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", "data-modifying CTE"),
        ("SELECT * FROM orders FOR UPDATE", "row lock"),
        ("SELECT * FROM orders FOR SHARE", "row lock"),
        ("SELECT * FROM orders FOR NO KEY UPDATE", "row lock"),
        ("SELECT pg_sleep(60)", "denial of service"),
        ("SELECT set_config('default_transaction_read_only', 'off', false)", "flips read-only"),
        ("SELECT pg_read_file('/etc/passwd')", "server files"),
        ("SELECT load_extension('evil')", "sqlite extension"),
        ("SELECT * FROM dblink('host=x', 'select 1') AS t(a int)", "other database"),
        ("SELECT * FROM OPENROWSET('x','y','z')", "sql server remote"),
    ],
)
def test_rejects_dangerous_constructs(sql, why):
    with pytest.raises(QueryRejected):
        check(sql)


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("SELECT 'unterminated FROM orders", "postgresql"),
        ("SELECT * FROM orders /* never closed", "postgresql"),
        ("SELECT $$ never closed FROM orders", "postgresql"),
        ("SELECT 'a\\'; DROP TABLE orders; --'", "mysql"),  # backslash-escaped quote
        ("SELECT E'it\\'s'", "postgresql"),
        ("SELECT 1 /*!50000 ; DROP TABLE orders */", "mysql"),  # MySQL runs this as code
        ("SELECT 1 --x; DROP TABLE orders", "mysql"),  # not a comment in MySQL without a space
        ("SELECT 1 # hidden\n; DROP TABLE orders", "mysql"),
    ],
)
def test_fails_closed_on_ambiguous_quoting_and_comments(sql, dialect):
    with pytest.raises(QueryRejected):
        check(sql, dialect=dialect)


def test_a_hash_is_only_a_comment_in_mysql():
    # In Postgres `#` is an operator, so what follows is still code and must be checked.
    with pytest.raises(QueryRejected):
        check("SELECT 1 # 2; DROP TABLE orders", dialect="postgresql")


@pytest.mark.parametrize("sql", ["", "   ", "\n\t", "-- only a comment", "SELECT 1\x00"])
def test_rejects_empty_or_odd_input(sql):
    with pytest.raises(QueryRejected):
        check(sql)


def test_rejects_over_long_sql():
    with pytest.raises(QueryRejected, match="limit"):
        check("SELECT " + "1," * 2000 + "1")


def test_rejects_sql_that_does_not_parse():
    with pytest.raises(QueryRejected):
        check("SELECT FROM WHERE")


# --- which tables a query touches ------------------------------------------------------------


def test_finds_every_table_including_joins_subqueries_and_unions():
    sql = """
        SELECT * FROM customers c
        JOIN orders o ON o.customer_id = c.id
        WHERE c.id IN (SELECT customer_id FROM reviews)
        UNION SELECT * FROM payments
    """
    assert check(sql).tables == {"customers", "orders", "reviews", "payments"}


def test_a_cte_is_not_a_table_but_what_it_reads_is():
    assert check("WITH t AS (SELECT * FROM secret) SELECT * FROM t").tables == {"secret"}
    assert check("WITH t AS (SELECT 1 AS a) SELECT * FROM t").tables == set()


def test_a_cte_named_like_a_real_table_cannot_hide_the_real_one():
    # The first subquery reads the real `orders`; only the second one sees the CTE of that name.
    sql = """
        SELECT * FROM (SELECT * FROM orders) x,
               (WITH orders AS (SELECT 1 AS a) SELECT * FROM orders) y
    """
    assert check(sql).tables == {"orders"}


def test_lateral_and_correlated_subqueries_are_covered():
    sql = "SELECT * FROM orders o, LATERAL (SELECT * FROM payments p WHERE p.order_id = o.id) x"
    assert check(sql).tables == {"orders", "payments"}


def test_default_schema_prefix_is_dropped_but_other_schemas_are_kept():
    assert check("SELECT * FROM public.orders").tables == {"orders"}
    assert check("SELECT * FROM sales.orders").tables == {"sales.orders"}
    assert check("SELECT * FROM PUBLIC.orders").tables == {"orders"}


def test_catalog_tables_and_cross_database_reads_are_named_so_they_can_be_denied():
    assert check("SELECT * FROM information_schema.tables").tables == {"information_schema.tables"}
    assert check(
        "SELECT * FROM otherdb.dbo.orders", dialect="mssql", default_schema="dbo"
    ).tables == {"otherdb.dbo.orders"}
    assert check("SELECT * FROM sqlite_master", dialect="sqlite", default_schema="main").tables == {
        "sqlite_master"
    }


def test_table_valued_functions_are_not_reported_as_tables():
    assert check("SELECT * FROM generate_series(1, 3) g").tables == set()


def test_table_names_keep_the_case_they_were_written_in():
    assert check('SELECT * FROM "Orders"').tables == {"Orders"}
