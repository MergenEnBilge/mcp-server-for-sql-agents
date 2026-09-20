"""Decides whether a piece of SQL may be run, and which tables it touches.

This is the first of two locks on `run_query`. The second is the database
itself (a read-only role or session), which holds even if this module has a
bug. So the rule throughout is: when in doubt, reject.

Three layers, cheapest first:
  1. Text pre-check. Comments and quoted text are blanked out, then we look for
     more than one statement, anything that isn't a SELECT/WITH, forbidden
     keywords (DROP, DELETE, INTO, ...) and dangerous functions.
  2. Structure check. sqlglot parses the SQL for the target engine. It must be
     exactly one query, containing no INSERT/UPDATE/DELETE/DDL nodes anywhere,
     including inside CTEs (`WITH x AS (DELETE ... RETURNING *) SELECT ...`).
  3. Table extraction. Every real table the query reads is collected, so the
     caller's table allowlist can be applied. CTE names are resolved by scope, so
     `WITH orders AS (...)` can't be used to sneak a real `orders` past the check.

Layer 1 is a regex, which SQL can always outsmart in principle (dialect-specific
quoting, for instance). It's a cheap early filter, not the boundary. Layer 2 and
the database role are what actually hold.
"""

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope

from mcp_sql_server.errors import QueryRejected

# SQLAlchemy dialect name -> sqlglot dialect name.
_SQLGLOT_DIALECTS = {
    "postgresql": "postgres",
    "mysql": "mysql",
    "mssql": "tsql",
    "sqlite": "sqlite",
}

# Statements and clauses that write, change structure, or read/write outside the database.
# (`INTO` covers `SELECT ... INTO new_table` and `INTO OUTFILE`.)
_FORBIDDEN_KEYWORDS = (
    "drop", "delete", "update", "alter", "insert", "truncate", "grant", "revoke", "create",
    "merge", "into", "call", "exec", "execute", "attach", "detach", "pragma", "vacuum",
    "copy", "reindex",
)  # fmt: skip

# Functions that touch the server itself: files, other databases, shell, session settings.
_DANGEROUS_FUNCTIONS = (
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_terminate_backend",
    "pg_cancel_backend", "pg_reload_conf", "set_config", "lo_import", "lo_export", "dblink",
    "load_extension", "load_file", "xp_cmdshell", "openrowset", "opendatasource",
    "sp_executesql", "sp_oacreate",
)  # fmt: skip

_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)
_FUNCTION_RE = re.compile(r"\b(" + "|".join(_DANGEROUS_FUNCTIONS) + r")\b", re.IGNORECASE)
_LOCKING_RE = re.compile(r"\bfor\s+(no\s+key\s+)?share\b", re.IGNORECASE)
_STARTS_LIKE_QUERY_RE = re.compile(r"^\(*\s*(select|with)\b", re.IGNORECASE)
_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_]*\$")

# sqlglot node types that mean "this is not a plain read". Looked up by name so a
# release that renames or drops one of them doesn't break import.
_WRITE_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command", "Into",
    "TruncateTable", "Grant", "Revoke", "Copy", "Set", "LoadData", "Use",
)  # fmt: skip
_WRITE_NODES = tuple(getattr(exp, name) for name in _WRITE_NODE_NAMES if hasattr(exp, name))


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    sql: str  # safe to send to the database (trailing semicolon removed)
    tables: frozenset[str]  # canonical names of every real table the query reads


class QueryValidator:
    def __init__(self, max_length: int = 20_000) -> None:
        self._max_length = max_length

    def validate(self, sql: str, *, dialect: str, default_schema: str | None) -> ValidatedQuery:
        if not sql or not sql.strip():
            raise QueryRejected("The SQL is empty.")
        if len(sql) > self._max_length:
            raise QueryRejected(f"The SQL is longer than the {self._max_length}-character limit.")
        if "\x00" in sql:
            raise QueryRejected("The SQL contains a null character.")

        masked = _mask_comments_and_quotes(sql, dialect)
        code = masked.strip().rstrip(";").strip()
        self._check_text(code)

        tree = self._parse(sql, dialect)
        tables = self._extract_tables(tree, default_schema)
        return ValidatedQuery(sql=_strip_trailing_semicolon(sql), tables=tables)

    # --- layer 1 --------------------------------------------------------------------------

    @staticmethod
    def _check_text(code: str) -> None:
        if ";" in code:
            raise QueryRejected("Only a single SQL statement is allowed.")
        if not _STARTS_LIKE_QUERY_RE.match(code):
            raise QueryRejected(
                "Only read-only SELECT queries are allowed (SELECT or WITH ... SELECT)."
            )
        if match := _FORBIDDEN_RE.search(code):
            raise QueryRejected(
                f"The keyword {match.group(1).upper()} is not allowed. "
                "Only read-only SELECT queries can be run."
            )
        if _LOCKING_RE.search(code):
            raise QueryRejected("Row-locking clauses (FOR SHARE / FOR UPDATE) are not allowed.")
        if match := _FUNCTION_RE.search(code):
            raise QueryRejected(f"The function {match.group(1)} is not allowed.")

    # --- layer 2 --------------------------------------------------------------------------

    @staticmethod
    def _parse(sql: str, dialect: str) -> exp.Query:
        try:
            statements = sqlglot.parse(sql, read=_SQLGLOT_DIALECTS.get(dialect))
        except SqlglotError as exc:
            raise QueryRejected(
                "The SQL could not be parsed as a single SELECT query. "
                "Check the syntax, or simplify the query."
            ) from exc
        parsed = [s for s in statements if s is not None]  # a trailing ';' yields None
        if len(parsed) != 1:
            raise QueryRejected("Only a single SQL statement is allowed.")
        tree = parsed[0]
        if not isinstance(tree, exp.Query):
            raise QueryRejected("Only read-only SELECT queries are allowed.")
        if tree.find(*_WRITE_NODES) is not None:
            raise QueryRejected("The query contains a data-changing or structural statement.")
        return tree

    # --- layer 3 --------------------------------------------------------------------------

    @staticmethod
    def _extract_tables(tree: exp.Query, default_schema: str | None) -> frozenset[str]:
        try:
            scopes = traverse_scope(tree)
        except SqlglotError as exc:
            raise QueryRejected(
                "The query's tables could not be determined. Try a simpler query."
            ) from exc
        found: set[str] = set()
        for scope in scopes:
            for source in scope.sources.values():
                # CTEs and subqueries show up as Scopes here; only real tables are exp.Table.
                if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
                    found.add(_canonical_table_name(source, default_schema))
        return frozenset(found)


def _canonical_table_name(table: exp.Table, default_schema: str | None) -> str:
    """`orders` for the default schema, `schema.orders` otherwise, `catalog.schema.orders` if
    a catalog was given (no allowlist entry will match that, so cross-database reads are denied)."""
    parts = [p for p in (table.catalog, table.db, table.name) if p]
    if len(parts) == 2 and default_schema and parts[0].casefold() == default_schema.casefold():
        parts = parts[1:]
    return ".".join(parts)


def _strip_trailing_semicolon(sql: str) -> str:
    stripped = sql.rstrip()
    return stripped[:-1].rstrip() if stripped.endswith(";") else stripped


def _mask_comments_and_quotes(sql: str, dialect: str) -> str:
    """Return the SQL with comments replaced by a space and quoted text replaced by `?`.

    What's left is only real SQL keywords and punctuation, so keyword checks can't be
    fooled by `SELECT 'DROP TABLE x'` (a harmless string) or hidden in a comment.

    Fails closed: anything that can't be interpreted the same way by every engine
    (unterminated quotes, backslashes inside quotes, MySQL's executable comments)
    is rejected rather than guessed at.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        two = sql[i : i + 2]

        if two == "--":
            # MySQL only treats `--` as a comment when whitespace follows (else it's two minuses).
            if dialect == "mysql" and i + 2 < n and sql[i + 2] not in " \t\r\n":
                raise QueryRejected("Put a space after '--' to start a comment.")
            i = _skip_to_line_end(sql, i)
            out.append(" ")
        elif c == "#" and dialect == "mysql":
            i = _skip_to_line_end(sql, i)
            out.append(" ")
        elif two == "/*":
            if sql[i + 2 : i + 3] == "!":
                raise QueryRejected("MySQL executable comments (/*! ... */) are not allowed.")
            end = sql.find("*/", i + 2)
            if end < 0:
                raise QueryRejected("The SQL has an unterminated comment.")
            i = end + 2
            out.append(" ")
        elif c in ("'", '"', "`") or (c == "[" and dialect == "mssql"):
            i = _skip_quoted(sql, i, "]" if c == "[" else c)
            out.append("?")
        elif c == "$" and not (i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == "_")):
            tag = _DOLLAR_TAG_RE.match(sql, i)
            if tag is None:
                out.append(c)
                i += 1
            else:
                end = sql.find(tag.group(), tag.end())
                if end < 0:
                    raise QueryRejected("The SQL has an unterminated dollar-quoted string.")
                i = end + len(tag.group())
                out.append("?")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _skip_to_line_end(sql: str, start: int) -> int:
    end = sql.find("\n", start)
    return len(sql) if end < 0 else end


def _skip_quoted(sql: str, start: int, close: str) -> int:
    """Index just past the quoted section that opens at `start`. A doubled closing quote
    is an escaped quote. Backslashes are refused because engines disagree on whether they
    escape (MySQL and Postgres E'' strings: yes; standard SQL and SQLite: no)."""
    j = start + 1
    n = len(sql)
    while j < n:
        ch = sql[j]
        if ch == "\\":
            raise QueryRejected(
                "Backslashes inside quoted text are not supported. Rewrite the query without them."
            )
        if ch == close:
            if j + 1 < n and sql[j + 1] == close:
                j += 2
                continue
            return j + 1
        j += 1
    raise QueryRejected("The SQL has an unterminated quoted string or identifier.")
