"""In-memory stand-ins so the service layer can be tested with no database and no network."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from mcp_sql_server.adapters.base import DBAdapter
from mcp_sql_server.config import QueryLimits
from mcp_sql_server.errors import TableNotFound
from mcp_sql_server.models import (
    AgentRecord,
    AuditEntry,
    Caller,
    ColumnInfo,
    ConnectionRecord,
    ExplainResult,
    RawResult,
    Relationship,
    SchemaSearchHit,
    TableDescription,
    TableInfo,
)
from mcp_sql_server.services.audit_service import AuditService
from mcp_sql_server.services.meta_store import DescriptionKey, MetaStore, Subjects
from mcp_sql_server.services.permission_service import TOOL_NAMES, PermissionService
from mcp_sql_server.services.query_service import QueryService
from mcp_sql_server.services.query_validator import QueryValidator
from mcp_sql_server.services.sanitizer import OutputSanitizer
from mcp_sql_server.services.schema_service import SchemaService

ANALYST = Caller(sub="user-1", name="Ana Lyst", roles=frozenset({"analyst"}))
# The same person, acting through an AI client. Tests decide what that client was approved for.
ANALYST_VIA_AGENT = ANALYST.model_copy(update={"client_id": "chat-client"})
# May call every tool, but has been given no connection or table access.
OUTSIDER = Caller(sub="user-2", name="No Access")


class FakeMetaStore(MetaStore):
    """Grants are stored as plain sets; lookups match a caller's subjects against them."""

    def __init__(self) -> None:
        self.connections: dict[str, ConnectionRecord] = {}
        self.connection_access: set[tuple[UUID, str, str]] = set()
        self.table_grants: set[tuple[UUID, str, str, str]] = set()
        self.tool_grants: set[tuple[str, str, str]] = set()
        self.curated: dict[UUID, dict[DescriptionKey, str]] = {}
        self.search_hits: list[SchemaSearchHit] = []
        self.search_calls: list[tuple[UUID, str, list[str], int]] = []
        self.audit: list[AuditEntry] = []
        self.fail_audit = False
        self.agents: dict[str, AgentRecord] = {}
        self.agent_sightings: list[tuple[str, str, str | None]] = []  # (client, user, name)
        self.max_pending = 100

    async def list_connections(self, subjects: Subjects) -> list[ConnectionRecord]:
        return [c for c in self.connections.values() if self._may_use(c.id, subjects)]

    async def get_connection(self, name: str, subjects: Subjects) -> ConnectionRecord | None:
        record = self.connections.get(name)
        return record if record and self._may_use(record.id, subjects) else None

    def _may_use(self, connection_id: UUID, subjects: Subjects) -> bool:
        return any((connection_id, t, i) in self.connection_access for t, i in subjects)

    async def allowed_tables(self, connection_id: UUID, subjects: Subjects) -> frozenset[str]:
        return frozenset(
            table
            for cid, t, i, table in self.table_grants
            if cid == connection_id and (t, i) in set(subjects)
        )

    async def allowed_tools(self, subjects: Subjects) -> frozenset[str]:
        return frozenset(tool for t, i, tool in self.tool_grants if (t, i) in set(subjects))

    async def get_agent(self, client_id: str) -> AgentRecord | None:
        return self.agents.get(client_id)

    async def register_agent(
        self, client_id: str, *, user_sub: str, user_name: str | None, reported_name: str | None
    ) -> bool:
        self.agent_sightings.append((client_id, user_sub, reported_name))
        known = self.agents.get(client_id)
        if known is not None:
            if reported_name:
                self.agents[client_id] = known.model_copy(update={"reported_name": reported_name})
            return True
        pending = sum(1 for a in self.agents.values() if a.status == "pending")
        if pending >= self.max_pending:
            return False
        self.agents[client_id] = AgentRecord(
            id=uuid4(), client_id=client_id, status="pending", reported_name=reported_name
        )
        return True

    async def descriptions(self, connection_id: UUID) -> dict[DescriptionKey, str]:
        return dict(self.curated.get(connection_id, {}))

    async def search_descriptions(
        self, connection_id: UUID, keyword: str, tables: Sequence[str], limit: int
    ) -> list[SchemaSearchHit]:
        self.search_calls.append((connection_id, keyword, list(tables), limit))
        return [hit for hit in self.search_hits if hit.table.casefold() in tables]

    async def write_audit(self, entry: AuditEntry) -> None:
        if self.fail_audit:
            raise ConnectionError("audit store is down")
        self.audit.append(entry)

    async def close(self) -> None:
        pass


def _columns(*names: str) -> tuple[ColumnInfo, ...]:
    return tuple(
        ColumnInfo(name=n, type="INTEGER", nullable=n != "id", primary_key=n == "id") for n in names
    )


def _table(name: str, columns: tuple[str, ...], *fks: tuple[str, str], comment=None):
    return TableDescription(
        name=name,
        kind="table",
        comment=comment,
        columns=_columns(*columns),
        foreign_keys=tuple(
            Relationship(from_table=name, columns=(col,), to_table=to, to_columns=("id",))
            for col, to in fks
        ),
    )


class FakeAdapter(DBAdapter):
    """Remembers every call so tests can check what did (and didn't) reach the database."""

    def __init__(self) -> None:
        self.tables = {
            t.name: t
            for t in (
                _table("customers", ("id", "email")),
                _table("orders", ("id", "customer_id"), ("customer_id", "customers")),
                _table("payments", ("id", "order_id"), ("order_id", "orders")),
                _table("categories", ("id", "parent_id"), ("parent_id", "categories")),
            )
        }
        self.result = RawResult(columns=["id"], rows=[(1,), (2,)], truncated=False)
        self.plan = ExplainResult(plan="Seq Scan on orders", estimated_cost=1.5, estimated_rows=70)
        self.error: Exception | None = None  # raised by execute/explain/sample_rows if set
        self.calls: list[tuple] = []

    dialect_name = "postgresql"
    default_schema = "public"

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def list_tables(self) -> list[TableInfo]:
        self.calls.append(("list_tables",))
        return [
            TableInfo(name=t.name, kind=t.kind, comment=t.comment) for t in self.tables.values()
        ]

    async def describe_table(self, table: str) -> TableDescription:
        self.calls.append(("describe_table", table))
        for name, description in self.tables.items():
            if name.casefold() == table.casefold():
                return description
        raise TableNotFound(f"Table '{table}' was not found or is not available to you.")

    async def execute(self, sql: str, *, max_rows: int, timeout_s: float) -> RawResult:
        self.calls.append(("execute", sql, max_rows, timeout_s))
        if self.error:
            raise self.error
        return self.result

    async def explain(self, sql: str, *, timeout_s: float) -> ExplainResult:
        self.calls.append(("explain", sql, timeout_s))
        if self.error:
            raise self.error
        return self.plan

    async def sample_rows(self, table: str, n: int, *, timeout_s: float) -> RawResult:
        self.calls.append(("sample_rows", table, n, timeout_s))
        if self.error:
            raise self.error
        return self.result

    def reached_database(self) -> bool:
        return any(call[0] in ("execute", "explain", "sample_rows") for call in self.calls)


class FakeProvider:
    def __init__(self, adapter: FakeAdapter) -> None:
        self.adapter = adapter

    async def adapter_for(self, record: ConnectionRecord) -> DBAdapter:
        return self.adapter


@dataclass
class Env:
    store: FakeMetaStore
    adapter: FakeAdapter
    connection_id: UUID
    limits: QueryLimits
    permissions: PermissionService
    query: QueryService
    schema: SchemaService


@pytest.fixture
def env() -> Env:
    """One connection, `shop`. The analyst role may use it, see customers/orders/categories
    (not payments), and call every tool. OUTSIDER may call every tool but has no connection access."""
    store = FakeMetaStore()
    adapter = FakeAdapter()
    connection_id = uuid4()
    store.connections["shop"] = ConnectionRecord(
        id=connection_id,
        name="shop",
        engine="postgresql",
        description="The online shop",
        details={},
        secret_encrypted=None,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    store.connection_access.add((connection_id, "role", "analyst"))
    for table in ("customers", "orders", "categories"):
        store.table_grants.add((connection_id, "role", "analyst", table))
    for tool in TOOL_NAMES:
        store.tool_grants.add(("role", "analyst", tool))
        store.tool_grants.add(("user", OUTSIDER.sub, tool))

    limits = QueryLimits(timeout_s=5.0, default_rows=500, max_rows=5000, max_sample_rows=100)
    permissions = PermissionService(store)
    audit = AuditService(store)
    sanitizer = OutputSanitizer(max_cell_chars=limits.max_cell_chars)
    provider = FakeProvider(adapter)
    return Env(
        store=store,
        adapter=adapter,
        connection_id=connection_id,
        limits=limits,
        permissions=permissions,
        query=QueryService(
            permissions, provider, audit, sanitizer, QueryValidator(limits.max_sql_length), limits
        ),
        schema=SchemaService(permissions, provider, audit, sanitizer, store),
    )
