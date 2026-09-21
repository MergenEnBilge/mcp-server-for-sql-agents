"""MySQL, against a real MySQL server: the adapter's promises (read-only, row cap, time limit,
schema reflection), the validator's MySQL rules, and the console's connection check.

Skipped when Docker or the MySQL driver (`pip install "mcp-server[mysql]"`) isn't available."""

import asyncio
import secrets
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

aiomysql = pytest.importorskip("aiomysql")

from gui_backend.connection_check import check_connection  # noqa: E402
from mcp_sql_server.adapters.sqlalchemy_adapter import SQLAlchemyAdapter  # noqa: E402
from mcp_sql_server.errors import (  # noqa: E402
    QueryFailed,
    QueryRejected,
    QueryTimeout,
    TableNotFound,
)
from mcp_sql_server.services.query_validator import QueryValidator  # noqa: E402

SETUP = [
    "CREATE DATABASE shop CHARACTER SET utf8mb4",
    "CREATE USER 'reader'@'%' IDENTIFIED BY '{reader}'",
    "GRANT SELECT ON shop.* TO 'reader'@'%'",
    # A user who *could* write, to prove the session itself is read-only.
    "CREATE USER 'writer'@'%' IDENTIFIED BY '{writer}'",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON shop.* TO 'writer'@'%'",
    """CREATE TABLE shop.customers (
           id INT PRIMARY KEY,
           email VARCHAR(100) NOT NULL COMMENT 'Login address',
           country CHAR(2)
       ) COMMENT = 'People who buy'""",
    """CREATE TABLE shop.`order` (
           id INT PRIMARY KEY,
           customer_id INT NOT NULL,
           total DECIMAL(10, 2),
           note TEXT,
           CONSTRAINT fk_order_customer FOREIGN KEY (customer_id) REFERENCES customers (id)
       )""",
    "CREATE VIEW shop.big_orders AS SELECT * FROM shop.`order` WHERE total > 100",
    "CREATE TABLE shop.numbers (n INT PRIMARY KEY)",
    "INSERT INTO shop.customers VALUES (1, 'a@x.test', 'DE'), (2, 'b@x.test', 'FR'), (3, 'c@x.test', NULL)",
    """INSERT INTO shop.`order` VALUES
           (1, 1, 50.00, 'first'), (2, 1, 150.50, NULL), (3, 2, 20.00, 'x'),
           (4, 2, 300.00, 'y'), (5, 3, 10.00, 'z'), (6, 3, 99.99, 'w')""",
    """INSERT INTO shop.numbers
           WITH RECURSIVE seq (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 500)
           SELECT n FROM seq""",
]


@dataclass(frozen=True)
class MySqlInfra:
    host: str
    port: int
    reader_password: str
    writer_password: str

    def url(self, user: str = "reader", password: str | None = None) -> str:
        secret = password or (self.reader_password if user == "reader" else self.writer_password)
        return f"mysql+aiomysql://{user}:{secret}@{self.host}:{self.port}/shop"

    def details(self, **overrides) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "database": "shop",
            "username": "reader",
            **overrides,
        }


async def _run_setup(host: str, port: int, root_password: str, statements: list[str]) -> None:
    deadline = asyncio.get_running_loop().time() + 120
    while True:
        try:
            conn = await aiomysql.connect(host=host, port=port, user="root", password=root_password)
            break
        except Exception:
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(1)
    try:
        async with conn.cursor() as cursor:
            for statement in statements:
                await cursor.execute(statement)
        await conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def mysql() -> Iterator[MySqlInfra]:
    try:
        import docker
        from testcontainers.core.container import DockerContainer

        docker.from_env().ping()
    except Exception as exc:
        pytest.skip(f"Docker is not available: {exc}")

    root, reader, writer = (secrets.token_hex(8) for _ in range(3))
    container = (
        DockerContainer("mysql:8.4").with_env("MYSQL_ROOT_PASSWORD", root).with_exposed_ports(3306)
    )
    with container:
        infra = MySqlInfra(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(3306)),
            reader_password=reader,
            writer_password=writer,
        )
        statements = [s.format(reader=reader, writer=writer) for s in SETUP]
        asyncio.run(_run_setup(infra.host, infra.port, root, statements))
        yield infra


@pytest.fixture
async def adapter(mysql):
    db = SQLAlchemyAdapter(mysql.url())
    await db.connect()
    yield db
    await db.close()


# --- reading the schema ---------------------------------------------------------------------------


async def test_it_reports_itself_as_mysql_and_knows_its_default_schema(adapter):
    assert (adapter.dialect_name, adapter.default_schema) == ("mysql", "shop")


async def test_tables_and_views_are_listed_with_their_comments(adapter):
    tables = {t.name: t for t in await adapter.list_tables()}
    assert set(tables) == {"customers", "order", "big_orders", "numbers"}
    assert tables["big_orders"].kind == "view" and tables["order"].kind == "table"
    assert tables["customers"].comment == "People who buy"


async def test_columns_keys_and_column_comments_are_described(adapter):
    order = await adapter.describe_table("order")
    columns = {c.name: c for c in order.columns}
    assert columns["id"].primary_key and not columns["id"].nullable
    assert columns["note"].nullable and "DECIMAL" in columns["total"].type.upper()
    fk = order.foreign_keys[0]
    assert (fk.columns, fk.to_table, fk.to_columns) == (("customer_id",), "customers", ("id",))

    customers = await adapter.describe_table("customers")
    assert {c.name: c.comment for c in customers.columns}["email"] == "Login address"


async def test_an_unknown_table_is_not_found(adapter):
    with pytest.raises(TableNotFound):
        await adapter.describe_table("nope")


# --- running queries -------------------------------------------------------------------------------


async def test_a_query_returns_rows_and_columns(adapter):
    result = await adapter.execute(
        "SELECT id, email FROM customers ORDER BY id", max_rows=10, timeout_s=5
    )
    assert result.columns == ["id", "email"]
    assert result.rows == [(1, "a@x.test"), (2, "b@x.test"), (3, "c@x.test")]
    assert result.truncated is False


async def test_the_row_cap_is_enforced_and_reported(adapter):
    result = await adapter.execute("SELECT n FROM numbers ORDER BY n", max_rows=7, timeout_s=5)
    assert len(result.rows) == 7 and result.truncated is True


async def test_sample_rows_are_limited(adapter):
    result = await adapter.sample_rows("order", 2, timeout_s=5)
    assert len(result.rows) == 2


async def test_colons_in_the_sql_are_not_mistaken_for_parameters(adapter):
    result = await adapter.execute("SELECT '12:30' AS t", max_rows=1, timeout_s=5)
    assert result.rows == [("12:30",)]


async def test_a_slow_query_is_cut_off_at_the_time_limit(adapter):
    # 500^4 rows that have to be sorted: nowhere near finishing in a second. (A bare count(*)
    # over the same join would be answered instantly from the row counts, so it isn't used.)
    slow = (
        "SELECT a.n FROM numbers a, numbers b, numbers c, numbers d "
        "ORDER BY a.n + b.n + c.n + d.n DESC"
    )
    with pytest.raises(QueryTimeout, match="1s time limit"):
        await adapter.execute(slow, max_rows=1, timeout_s=1)
    # The connection pool is still healthy afterwards.
    assert (await adapter.execute("SELECT 1", max_rows=1, timeout_s=5)).rows == [(1,)]


async def test_a_database_error_comes_back_as_a_short_readable_message(adapter):
    with pytest.raises(QueryFailed) as failure:
        await adapter.execute("SELECT nope FROM customers", max_rows=1, timeout_s=5)
    assert "nope" in str(failure.value) and len(str(failure.value)) < 300


async def test_explain_returns_a_plan(adapter):
    plan = await adapter.explain("SELECT * FROM customers WHERE id = 1", timeout_s=5)
    assert "customers" in plan.plan


# --- read-only, twice ------------------------------------------------------------------------------------


async def test_a_user_who_can_only_read_cannot_write_even_if_the_validator_were_bypassed(adapter):
    with pytest.raises(QueryFailed, match="denied"):
        await adapter.execute("DELETE FROM customers", max_rows=1, timeout_s=5)


async def test_the_session_is_read_only_even_for_a_user_who_may_write(mysql):
    """The adapter opens every MySQL connection as READ ONLY, so a role that has been granted
    too much still can't change anything through this server."""
    privileged = SQLAlchemyAdapter(mysql.url("writer"))
    await privileged.connect()
    try:
        with pytest.raises(QueryFailed, match="READ ONLY"):
            await privileged.execute(
                "INSERT INTO customers VALUES (99, 'z@x.test', 'US')", max_rows=1, timeout_s=5
            )
        count = await privileged.execute(
            "SELECT count(*) FROM customers WHERE id = 99", max_rows=1, timeout_s=5
        )
        assert count.rows == [(0,)]
    finally:
        await privileged.close()


# --- MySQL's own SQL rules, through the validator into the real database ----------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM `order` WHERE total > 100",
        "SELECT c.email, count(*) FROM customers c JOIN `order` o ON o.customer_id = c.id GROUP BY c.email",
        "SELECT id # a MySQL comment\n FROM customers",
        "SELECT id -- a standard comment\n FROM customers",
        "WITH big AS (SELECT * FROM `order` WHERE total > 100) SELECT count(*) FROM big",
    ],
)
async def test_ordinary_mysql_queries_validate_and_run(adapter, sql):
    checked = QueryValidator().validate(sql, dialect="mysql", default_schema=adapter.default_schema)
    result = await adapter.execute(checked.sql, max_rows=100, timeout_s=5)
    assert result.rows


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE customers",
        "SELECT * FROM customers INTO OUTFILE '/tmp/x'",
        "SELECT /*! SLEEP(5) */ 1",
        "SELECT 1--1",
        "SELECT LOAD_FILE('/etc/passwd')",
        "DELETE FROM customers",
    ],
)
async def test_dangerous_mysql_queries_never_reach_the_database(sql):
    with pytest.raises(QueryRejected):
        QueryValidator().validate(sql, dialect="mysql", default_schema="shop")


async def test_the_tables_a_query_reads_are_found_through_backticks_and_aliases():
    checked = QueryValidator().validate(
        "SELECT o.id FROM `shop`.`order` o JOIN customers c ON c.id = o.customer_id",
        dialect="mysql",
        default_schema="shop",
    )
    assert checked.tables == {"order", "customers"}


# --- the console's "test connection" ------------------------------------------------------------------------


async def test_the_console_can_test_a_mysql_connection(mysql):
    result = await check_connection("mysql", mysql.details(), mysql.reader_password, timeout_s=15)
    assert result.ok and "Found 4 tables" in result.message


async def test_a_wrong_password_is_reported_as_such_without_echoing_it(mysql):
    result = await check_connection("mysql", mysql.details(), "not-the-password", timeout_s=15)
    assert not result.ok
    assert "authentication failed for user 'reader'" in result.message
    assert "not-the-password" not in result.message


async def test_a_database_that_is_not_there_is_reported_as_such(mysql):
    result = await check_connection(
        "mysql", mysql.details(database="nope"), mysql.reader_password, timeout_s=15
    )
    assert not result.ok
    assert "can't use the database 'nope'" in result.message
