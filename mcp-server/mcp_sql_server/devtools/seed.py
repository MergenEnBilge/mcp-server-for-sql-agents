"""Development helper: registers the sample databases in app_meta and grants access to them.

This is what an admin would do through the GUI (deliverable 7); until that exists,
this script does it directly so the MCP server has something to talk to. It writes
through the same tables and the same encryption the real registry reads.

    cd mcp-server
    python -m mcp_sql_server.devtools.seed

What it sets up, for the role `analyst` (change with --role):
  * connections `shop-pg` (the Postgres org_data database, read-only role) and
    `shop-sqlite` (the SQLite copy of the same data)
  * access to both, to every table except `payments` (so the table allowlist has
    something to hide), and to every MCP tool
  * human-written descriptions of the tables and key columns, for describe_table
    and search_schema

Safe to run repeatedly. Never use against a real deployment: it wipes and recreates
the role's table grants on these two connections.
"""

import argparse
import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.services.permission_service import TOOL_NAMES

SAMPLE_TABLES = (
    "categories",
    "customers",
    "order_items",
    "orders",
    "payments",
    "products",
    "reviews",
)

# Table-level descriptions have column None.
DESCRIPTIONS: dict[tuple[str, str | None], str] = {
    ("customers", None): "People who have bought (or registered to buy) from the shop.",
    ("customers", "country"): "Customer's home country as an ISO 3166-1 alpha-2 code, e.g. DE.",
    ("customers", "is_active"): "False for closed accounts.",
    ("categories", None): "Product categories. Sub-categories point at their parent.",
    (
        "categories",
        "parent_id",
    ): "The category this one sits under; empty for top-level categories.",
    ("products", None): "Items the shop sells, each in exactly one category.",
    (
        "products",
        "unit_price",
    ): "Current list price. Old orders keep the price they were bought at.",
    (
        "orders",
        None,
    ): "One row per checkout. What was bought is in order_items; payment is in payments.",
    (
        "orders",
        "status",
    ): "Lifecycle stage: pending, paid, shipped, delivered, cancelled or refunded.",
    ("orders", "shipping_country"): "Destination country as an ISO 3166-1 alpha-2 code.",
    ("order_items", None): "The products inside an order, one row per product per order.",
    (
        "order_items",
        "unit_price",
    ): "Price per unit at the time of purchase (may differ from products.unit_price).",
    ("payments", None): "Payment attempts against orders: captured, refunded or failed.",
    ("reviews", None): "Customer ratings and free-text comments about products.",
    ("reviews", "rating"): "Whole stars from 1 (worst) to 5 (best).",
    ("reviews", "body"): "Free text written by the customer.",
}


@dataclass(frozen=True)
class PostgresTarget:
    host: str
    port: int
    database: str
    username: str
    password: str


async def seed_sample_registry(
    engine: AsyncEngine,
    secret_box: SecretBox,
    *,
    postgres: PostgresTarget | None,
    sqlite_path: str | None,
    role: str = "analyst",
    hidden_tables: Sequence[str] = ("payments",),
) -> None:
    """Register the sample connections and grant `role` access. `engine` must be able to
    write to app_meta (the migrations owner, or the GUI's role)."""
    visible = [t for t in SAMPLE_TABLES if t not in hidden_tables]
    async with engine.begin() as conn:
        for tool in sorted(TOOL_NAMES):
            await conn.execute(
                text(
                    "INSERT INTO tool_permissions (subject_type, subject_id, tool_name) "
                    "VALUES ('role', :role, :tool) ON CONFLICT DO NOTHING"
                ),
                {"role": role, "tool": tool},
            )

        targets: list[tuple[str, str, str, dict[str, object], str | None]] = []
        if postgres is not None:
            targets.append(
                (
                    "shop-pg",
                    "postgresql",
                    "Sample online shop in Postgres (read-only).",
                    {
                        "host": postgres.host,
                        "port": postgres.port,
                        "database": postgres.database,
                        "username": postgres.username,
                    },
                    secret_box.encrypt(postgres.password),
                )
            )
        if sqlite_path is not None:
            targets.append(
                (
                    "shop-sqlite",
                    "sqlite",
                    "The same sample shop, as a SQLite file (read-only).",
                    {"path": str(Path(sqlite_path).resolve())},
                    None,
                )
            )

        for name, engine_name, description, details, secret in targets:
            connection_id = (
                await conn.execute(
                    text(
                        "INSERT INTO connections "
                        "(name, engine, description, details, secret_encrypted) "
                        "VALUES (:name, :engine, :description, :details, :secret) "
                        "ON CONFLICT (name) DO UPDATE SET engine = EXCLUDED.engine, "
                        "description = EXCLUDED.description, details = EXCLUDED.details, "
                        "secret_encrypted = EXCLUDED.secret_encrypted, is_active = true "
                        "RETURNING id"
                    ).bindparams(bindparam("details", type_=JSONB)),
                    {
                        "name": name,
                        "engine": engine_name,
                        "description": description,
                        "details": details,
                        "secret": secret,
                    },
                )
            ).scalar_one()

            await conn.execute(
                text(
                    "INSERT INTO connection_access (connection_id, subject_type, subject_id) "
                    "VALUES (:id, 'role', :role) ON CONFLICT DO NOTHING"
                ),
                {"id": connection_id, "role": role},
            )
            await conn.execute(
                text(
                    "DELETE FROM table_permissions WHERE connection_id = :id "
                    "AND subject_type = 'role' AND subject_id = :role"
                ),
                {"id": connection_id, "role": role},
            )
            for table in visible:
                await conn.execute(
                    text(
                        "INSERT INTO table_permissions (connection_id, subject_type, subject_id, "
                        "table_name) VALUES (:id, 'role', :role, :table)"
                    ),
                    {"id": connection_id, "role": role, "table": table},
                )
            for (table, column), description in DESCRIPTIONS.items():
                await conn.execute(
                    text(
                        "INSERT INTO schema_descriptions (connection_id, table_name, column_name, "
                        "description) VALUES (:id, :table, :column, :description) "
                        "ON CONFLICT (connection_id, table_name, column_name) "
                        "DO UPDATE SET description = EXCLUDED.description"
                    ),
                    {
                        "id": connection_id,
                        "table": table,
                        "column": column,
                        "description": description,
                    },
                )


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--role", default="analyst", help="role to grant access to")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    from dotenv import load_dotenv

    load_dotenv(root / ".env")
    engine = create_async_engine(os.environ["APP_META_MIGRATIONS_URL"])
    secret_box = SecretBox(os.environ["MCP_CONNECTION_SECRET_KEYS"])
    sqlite_path = root / "db" / "sample_data" / "sample.sqlite"

    async def run() -> None:
        try:
            await seed_sample_registry(
                engine,
                secret_box,
                postgres=PostgresTarget(
                    host="localhost",
                    port=int(os.environ.get("POSTGRES_PORT", "5432")),
                    database="org_data",
                    username="org_readonly",
                    password=os.environ["ORG_READONLY_PASSWORD"],
                ),
                sqlite_path=str(sqlite_path) if sqlite_path.exists() else None,
                role=args.role,
            )
        finally:
            await engine.dispose()

    asyncio.run(run())
    print(f"registered shop-pg and shop-sqlite for role '{args.role}' (payments table hidden)")


if __name__ == "__main__":
    _main()
