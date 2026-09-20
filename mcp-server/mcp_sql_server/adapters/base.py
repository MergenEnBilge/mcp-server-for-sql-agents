"""The contract between the service layer and a target database.

The service layer only ever talks to `DBAdapter`. Supporting a new engine means
writing a new adapter class; nothing in the services or tools changes.

`SQLAlchemyAdapter` covers every engine SQLAlchemy supports (PostgreSQL, MySQL/
MariaDB, SQL Server, SQLite, ...). Warehouse engines such as Snowflake and
BigQuery don't fit SQLAlchemy's async model cleanly, so they are a roadmap item.
When they arrive they should be their own `DBAdapter` subclasses (using their
own async or thread-wrapped clients), implementing exactly this interface.
"""

from abc import ABC, abstractmethod

from mcp_sql_server.models import ExplainResult, RawResult, TableDescription, TableInfo


class DBAdapter(ABC):
    """One connection to one target database. Read-only by contract.

    Implementations must enforce read-only access and the row/time limits
    themselves, using whatever the engine offers, and must never trust that
    the caller already checked the SQL.
    """

    @property
    @abstractmethod
    def dialect_name(self) -> str:
        """SQLAlchemy-style engine name: 'postgresql', 'mysql', 'mssql', 'sqlite', ..."""

    @property
    @abstractmethod
    def default_schema(self) -> str | None:
        """Schema that unqualified table names live in (None if the engine has no such notion).

        Only valid after `connect()`.
        """

    @abstractmethod
    async def connect(self) -> None:
        """Open the connection pool and verify the database is reachable."""

    @abstractmethod
    async def close(self) -> None:
        """Release all connections. The adapter can't be used afterwards."""

    @abstractmethod
    async def list_tables(self) -> list[TableInfo]:
        """Tables and views the adapter can see, with canonical names (see models.py)."""

    @abstractmethod
    async def describe_table(self, table: str) -> TableDescription:
        """Columns, primary key and foreign keys. Raises TableNotFound for unknown tables."""

    @abstractmethod
    async def execute(self, sql: str, *, max_rows: int, timeout_s: float) -> RawResult:
        """Run one read-only statement and return at most `max_rows` rows.

        `RawResult.truncated` is True when the statement had more rows than that.
        Raises QueryTimeout after `timeout_s` and QueryFailed for database errors.
        """

    @abstractmethod
    async def explain(self, sql: str, *, timeout_s: float) -> ExplainResult:
        """Query plan for `sql` without running it. Raises ExplainNotSupported if the
        engine can't do that."""

    @abstractmethod
    async def sample_rows(self, table: str, n: int, *, timeout_s: float) -> RawResult:
        """The first `n` rows of a table. Builds the query itself so identifier quoting
        and row-limit syntax are always right for the engine."""
