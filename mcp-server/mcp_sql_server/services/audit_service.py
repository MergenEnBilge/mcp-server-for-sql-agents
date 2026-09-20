"""Records every tool call: who, what, against which database, and how it went.

Wrap a tool call in `async with audit.record(...)`. The entry is written whether
the call succeeds, is refused, or fails, so denied attempts show up in the log too.

If the entry can't be written, the caller does NOT get the result. Handing back
data with no record of it having been read is the one thing an audit log must
never allow, so an outage in the audit store fails the tool call instead.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp_sql_server.errors import AuditWriteError, McpSqlError
from mcp_sql_server.models import AuditEntry, Caller
from mcp_sql_server.services.meta_store import MetaStore

logger = logging.getLogger(__name__)

_MAX_ERROR_CHARS = 1000


@dataclass(slots=True)
class AuditRecorder:
    """What a tool call fills in as it learns things, for the entry written at the end."""

    connection_name: str | None
    tables: list[str] = field(default_factory=list)
    row_count: int | None = None
    summary: str | None = None


class AuditService:
    def __init__(self, store: MetaStore) -> None:
        self._store = store

    @asynccontextmanager
    async def record(
        self,
        caller: Caller,
        tool: str,
        arguments: dict[str, Any],
        connection_name: str | None = None,
    ) -> AsyncIterator[AuditRecorder]:
        recorder = AuditRecorder(connection_name=connection_name)
        started = time.monotonic()
        failure: BaseException | None = None
        try:
            yield recorder
        except Exception as exc:
            failure = exc
            raise
        finally:
            entry = AuditEntry(
                caller_sub=caller.sub,
                caller_name=caller.name,
                tool_name=tool,
                connection_name=recorder.connection_name,
                arguments=arguments,
                duration_ms=int((time.monotonic() - started) * 1000),
                success=failure is None,
                tables=recorder.tables,
                row_count=recorder.row_count,
                result_summary=recorder.summary,
                error_message=_describe(failure) if failure else None,
                caller_roles=sorted(caller.roles),
            )
            try:
                await self._store.write_audit(entry)
            except Exception as audit_error:
                logger.exception("could not write audit entry for tool %s", tool)
                if failure is None:
                    raise AuditWriteError(
                        "The request could not be recorded in the audit log, "
                        "so its result is being withheld. Try again later."
                    ) from audit_error


def _describe(error: BaseException) -> str:
    """Domain errors are written for humans and safe to store as-is. Anything else is a bug,
    so store only its type here and leave the details to the server log."""
    if isinstance(error, McpSqlError):
        return str(error)[:_MAX_ERROR_CHARS]
    logger.error("unexpected error in tool call", exc_info=error)
    return f"internal error ({type(error).__name__})"
