"""Errors the service layer raises.

Every message here is written to be shown to the LLM that made the request, so
it must never contain secrets, stack traces or anything about the system that
the caller isn't entitled to know. That's why "no such connection" and "you
aren't allowed to use that connection" are deliberately the same error.
"""


class McpSqlError(Exception):
    """Base class. `str(error)` is safe to return to the caller."""


class ToolNotPermitted(McpSqlError):
    """The caller has no grant for this tool."""


class ConnectionNotFound(McpSqlError):
    """Unknown, inactive, or not accessible to the caller (indistinguishable on purpose)."""


class ConnectionUnavailable(McpSqlError):
    """The connection exists but the database can't be reached or is misconfigured.
    Details go to the server log, not to the caller."""


class TableNotFound(McpSqlError):
    """Unknown, or not accessible to the caller (indistinguishable on purpose)."""


class InvalidArgument(McpSqlError):
    """A tool argument is out of range or malformed."""


class QueryRejected(McpSqlError):
    """The SQL was refused before reaching the database (not read-only, unparseable, ...)."""


class QueryTimeout(McpSqlError):
    """The query ran past its time limit and was cancelled."""


class QueryFailed(McpSqlError):
    """The database rejected or failed the query. Message is the (shortened) driver error."""


class ExplainNotSupported(McpSqlError):
    """This database engine can't produce a plan without running the query."""


class AuditWriteError(McpSqlError):
    """The audit row could not be saved, so the result is withheld."""
