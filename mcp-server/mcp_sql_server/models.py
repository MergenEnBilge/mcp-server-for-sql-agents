"""Data shapes shared across the layers.

Two families:
  * plain dataclasses for what database adapters return (fast, no validation, engine-neutral)
  * pydantic models for what callers see (these become the MCP tool result schemas)
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TableKind = Literal["table", "view"]


# --- who is calling -----------------------------------------------------------------------


class Caller(BaseModel):
    """The authenticated identity behind a request, taken from the OAuth token."""

    model_config = ConfigDict(frozen=True)

    sub: str = Field(min_length=1)
    name: str | None = None
    roles: frozenset[str] = frozenset()
    # The AI client acting for this person (the OAuth client the token was issued to). None only
    # for the local stdio transport, which has no login and is trusted by whoever configured it.
    client_id: str | None = None

    def subjects(self) -> tuple[tuple[str, str], ...]:
        """Everything a permission can be granted to for this caller: their own user id,
        plus each of their roles. A grant to any one of these applies."""
        return (("user", self.sub), *(("role", role) for role in sorted(self.roles)))


AgentStatus = Literal["pending", "approved", "blocked"]
AgentState = Literal["pending", "approved", "blocked", "expired"]


class AgentRecord(BaseModel):
    """An AI client the server has seen, and what an administrator allowed it (see 0003)."""

    id: UUID
    client_id: str
    label: str = ""
    reported_name: str | None = None
    status: AgentStatus
    allowed_tools: list[str] = Field(default_factory=list)
    all_connections: bool = True
    connection_ids: list[UUID] = Field(default_factory=list)
    expires_at: datetime | None = None

    def state(self, now: datetime | None = None) -> AgentState:
        """`approved` turns into `expired` once the approval's end date has passed."""
        if self.status == "approved" and self.expires_at is not None:
            if self.expires_at <= (now or datetime.now(UTC)):
                return "expired"
        return self.status


# --- what adapters return -----------------------------------------------------------------
# Table names are "canonical": plain `orders` for the connection's default schema,
# `schema.orders` for any other schema. That form is what permissions are written in.


@dataclass(frozen=True, slots=True)
class TableInfo:
    name: str
    kind: TableKind
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    primary_key: bool
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class Relationship:
    """A foreign key: `from_table.columns` references `to_table.to_columns`."""

    from_table: str
    columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableDescription:
    name: str
    kind: TableKind
    comment: str | None
    columns: tuple[ColumnInfo, ...]
    foreign_keys: tuple[Relationship, ...]


@dataclass(frozen=True, slots=True)
class RawResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool  # more rows existed than the cap allowed


@dataclass(frozen=True, slots=True)
class ExplainResult:
    plan: str
    estimated_cost: float | None = None  # engine's own cost units; only some engines report it
    estimated_rows: int | None = None


# --- connection registry ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConnectionRecord:
    """A row of app_meta.connections. `secret_encrypted` stays inside the service layer."""

    id: UUID
    name: str
    engine: str
    description: str
    details: dict[str, Any]
    secret_encrypted: str | None
    updated_at: datetime


# --- what callers see ---------------------------------------------------------------------


class SecurityFlag(BaseModel):
    """Marks content that was withheld or altered because it looked unsafe to hand to an LLM."""

    location: str
    rule: str


class ConnectionInfo(BaseModel):
    name: str
    engine: str
    description: str


class TableSummary(BaseModel):
    name: str
    kind: TableKind
    description: str = ""


class ColumnDetail(BaseModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool
    description: str = ""


class ForeignKeyDetail(BaseModel):
    from_table: str
    columns: list[str]
    to_table: str
    to_columns: list[str]


class TableDetail(BaseModel):
    name: str
    kind: TableKind
    description: str = ""
    columns: list[ColumnDetail]
    foreign_keys: list[ForeignKeyDetail]
    security_flags: list[SecurityFlag] = Field(default_factory=list)


class TableRelationships(BaseModel):
    table: str
    references: list[ForeignKeyDetail]  # foreign keys this table has, pointing at other tables
    referenced_by: list[ForeignKeyDetail]  # foreign keys elsewhere that point at this table


class SchemaSearchHit(BaseModel):
    table: str
    column: str | None  # None means the hit is on the table itself
    description: str
    score: float


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int
    security_flags: list[SecurityFlag] = Field(default_factory=list)


class PlanResult(BaseModel):
    plan: str
    estimated_cost: float | None = None
    estimated_rows: int | None = None


# --- audit ----------------------------------------------------------------------------------


@dataclass(slots=True)
class AuditEntry:
    caller_sub: str
    caller_name: str | None
    tool_name: str
    connection_name: str | None
    arguments: dict[str, Any]
    duration_ms: int
    success: bool
    tables: list[str] = field(default_factory=list)
    row_count: int | None = None
    result_summary: str | None = None
    error_message: str | None = None
    caller_roles: list[str] = field(default_factory=list)  # so the GUI can list roles it has seen
    client_id: str | None = None  # which AI client made the call
