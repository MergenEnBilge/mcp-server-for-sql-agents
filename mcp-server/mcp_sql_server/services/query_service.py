"""Running queries: run_query, explain_query and get_sample_rows.

Order of events for run_query, and why:
  1. tool and connection permission  (cheap, and nothing else should happen without it)
  2. validate the SQL                (single read-only SELECT; find the tables it reads)
  3. table allowlist                 (every table it reads must be one the caller may see)
  4. execute with row and time caps  (the adapter enforces both, plus a read-only session)
  5. sanitize what came back         (nothing from the database may act as an instruction)
The audit entry is written last, whatever happened above.

The database's own read-only role is a separate, independent lock behind steps 2 and 3.
"""

import time
from typing import Any

from mcp_sql_server.adapters.base import DBAdapter
from mcp_sql_server.config import QueryLimits
from mcp_sql_server.errors import InvalidArgument, TableNotFound
from mcp_sql_server.models import Caller, PlanResult, QueryResult, RawResult, SecurityFlag
from mcp_sql_server.services.audit_service import AuditRecorder, AuditService
from mcp_sql_server.services.connection_registry import AdapterProvider
from mcp_sql_server.services.permission_service import ConnectionAccess, PermissionService
from mcp_sql_server.services.query_validator import QueryValidator, ValidatedQuery
from mcp_sql_server.services.sanitizer import OutputSanitizer
from mcp_sql_server.services.serialization import to_json_safe
from mcp_sql_server.services.tool_service import ToolService


class QueryService(ToolService):
    def __init__(
        self,
        permissions: PermissionService,
        adapters: AdapterProvider,
        audit: AuditService,
        sanitizer: OutputSanitizer,
        validator: QueryValidator,
        limits: QueryLimits,
    ) -> None:
        super().__init__(permissions, adapters, audit, sanitizer)
        self._validator = validator
        self._limits = limits

    async def run_query(
        self, caller: Caller, connection_name: str, sql: str, row_limit: int | None = None
    ) -> QueryResult:
        args = {"connection_name": connection_name, "sql": sql, "row_limit": row_limit}
        async with self._audit.record(caller, "run_query", args, connection_name) as rec:
            access, adapter = await self._open(caller, "run_query", connection_name)
            limit = self._resolve_row_limit(row_limit)
            query = self._validate_and_authorize(sql, adapter, access, rec)

            started = time.monotonic()
            raw = await adapter.execute(query.sql, max_rows=limit, timeout_s=self._limits.timeout_s)
            result = self._to_result(raw, started)

            rec.row_count = result.row_count
            rec.summary = _result_summary(result)
            return result

    async def explain_query(self, caller: Caller, connection_name: str, sql: str) -> PlanResult:
        args = {"connection_name": connection_name, "sql": sql}
        async with self._audit.record(caller, "explain_query", args, connection_name) as rec:
            access, adapter = await self._open(caller, "explain_query", connection_name)
            query = self._validate_and_authorize(sql, adapter, access, rec)

            explained = await adapter.explain(query.sql, timeout_s=self._limits.timeout_s)
            flags: list[SecurityFlag] = []
            plan = self._sanitizer.clean_text(explained.plan, "query plan", flags)
            rec.summary = "plan returned" if not flags else "plan withheld by the sanitizer"
            return PlanResult(
                plan=plan,
                estimated_cost=explained.estimated_cost,
                estimated_rows=explained.estimated_rows,
            )

    async def get_sample_rows(
        self, caller: Caller, connection_name: str, table: str, n: int = 5
    ) -> QueryResult:
        args = {"connection_name": connection_name, "table_name": table, "n": n}
        async with self._audit.record(caller, "get_sample_rows", args, connection_name) as rec:
            access, adapter = await self._open(caller, "get_sample_rows", connection_name)
            if not 1 <= n <= self._limits.max_sample_rows:
                raise InvalidArgument(f"n must be between 1 and {self._limits.max_sample_rows}.")
            access.require_table(table)
            rec.tables = [table]

            started = time.monotonic()
            raw = await adapter.sample_rows(table, n, timeout_s=self._limits.timeout_s)
            result = self._to_result(raw, started)

            rec.row_count = result.row_count
            rec.summary = _result_summary(result)
            return result

    # --- shared steps ---------------------------------------------------------------------

    def _resolve_row_limit(self, requested: int | None) -> int:
        if requested is None:
            return self._limits.default_rows
        if not 1 <= requested <= self._limits.max_rows:
            raise InvalidArgument(f"row_limit must be between 1 and {self._limits.max_rows}.")
        return requested

    def _validate_and_authorize(
        self, sql: str, adapter: DBAdapter, access: ConnectionAccess, rec: AuditRecorder
    ) -> ValidatedQuery:
        query = self._validator.validate(
            sql, dialect=adapter.dialect_name, default_schema=adapter.default_schema
        )
        # Recorded before the allowlist check so a refused attempt still shows which tables
        # the caller was reaching for.
        rec.tables = sorted(query.tables)
        for table in sorted(query.tables):
            if not access.allows(table):
                raise TableNotFound(f"Table '{table}' was not found or is not available to you.")
        return query

    def _to_result(self, raw: RawResult, started: float) -> QueryResult:
        flags: list[SecurityFlag] = []
        clean = self._sanitizer
        columns = [clean.clean_text(c, "column name", flags) for c in raw.columns]
        rows: list[list[Any]] = [
            [
                clean.clean_value(to_json_safe(value), f"row {r}, column {raw.columns[c]}", flags)
                for c, value in enumerate(row)
            ]
            for r, row in enumerate(raw.rows, start=1)
        ]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=raw.truncated,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            security_flags=flags,
        )


def _result_summary(result: QueryResult) -> str:
    parts = [f"{result.row_count} rows"]
    if result.truncated:
        parts.append("truncated at the row limit")
    if result.security_flags:
        parts.append(f"{len(result.security_flags)} value(s) withheld by the sanitizer")
    return "; ".join(parts)
