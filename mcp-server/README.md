# mcp-server

The part of the project that sits between an LLM and your databases. This folder
currently holds the **adapter and service layers**, which are everything except the MCP
tool wiring and authentication (both come next). The services can be used and tested on their
own, with no MCP and no network involved.

## How it's layered

```
   MCP tools (thin wrappers)          <- next step: just translate MCP calls into service calls
            │
   services/                          <- all the real logic: who may do what, is this SQL safe,
   ├─ permission_service                 what came back, what to write in the audit log
   ├─ schema_service    (list_tables, describe_table, get_relationships, search_schema, ...)
   ├─ query_service     (run_query, explain_query, get_sample_rows)
   ├─ query_validator   (is this one read-only SELECT? which tables does it read?)
   ├─ sanitizer         (nothing coming out of a database may act as an instruction)
   ├─ audit_service     (one row per call, written even when the call is refused)
   ├─ connection_registry (turns a stored connection record into a live adapter)
   └─ meta_store        (reads/writes app_meta, which is always Postgres)
            │
   adapters/                          <- one class per kind of database
   ├─ base.py            DBAdapter: the contract the services depend on
   └─ sqlalchemy_adapter.py   works for Postgres, MySQL, SQL Server, SQLite from a URL alone
```

Adding a new kind of database means writing a new `DBAdapter` subclass. Nothing in the
services changes. Snowflake and BigQuery don't fit SQLAlchemy's async model cleanly, so
they're on the roadmap as adapters of their own.

## What protects the data

Every rule is enforced in the service layer **and** again by the database, so a bug in
one doesn't open a hole.

| Property | Service layer | Database |
|---|---|---|
| Read-only | Only a single `SELECT`/`WITH` is accepted; `INTO`, DML, DDL and data-modifying CTEs are refused, checked by keyword scan *and* by parsing the SQL | Postgres: read-only transaction that can't be switched back, plus a role that only has `SELECT`. SQLite: file opened `mode=ro` plus `PRAGMA query_only`. |
| Table allowlist | The parser lists every real table a query reads (CTEs resolved properly, so `WITH orders AS (...)` can't hide a real `orders`); any table the caller can't see is refused | – (one shared read-only role can't express per-user tables) |
| Row cap (default 500, max 5000) | Rejects out-of-range requests | The result is read in a stream and cut off at the cap |
| Time limit (default 5s) | Passed to the adapter | Postgres `statement_timeout`; SQLite aborts the query at the deadline; MySQL `max_execution_time` |
| Audit trail | One row per call, refused ones included. If the row can't be saved, the result is withheld | The `mcp_app` role can append to the log but not read, edit or delete it |
| Untrusted output | Cell text that reads like instructions to an AI is withheld and reported; invisible characters stripped; long text cut | – |
| Secrets | Connection passwords are Fernet-encrypted at rest, decrypted in memory only to open a connection, never logged | The key is in the environment, not in the database |

Error messages are written to be shown to the LLM, so they're deliberately vague where
detail would leak: an unknown connection and a forbidden one give the same message, and
so do an unknown table and one the caller can't see.

## Running it

You need the dev database up (see [db/README.md](../db/README.md)), then:

```bash
cd mcp-server
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python -m mcp_sql_server.devtools.seed   # registers the sample databases for the role "analyst"
```

The seed step does what the admin GUI will do later: it registers `shop-pg` and `shop-sqlite`,
grants the `analyst` role access to every table except `payments`, and adds table and column
descriptions. It reads `.env` from the repo root.

Settings come from environment variables (or `.env`); see [.env.example](../.env.example).
The ones the server needs are `MCP_APP_META_URL` and `MCP_CONNECTION_SECRET_KEYS`, and the
query limits are tunable with `MCP_DEFAULT_QUERY_TIMEOUT_S`, `MCP_MAX_ROW_LIMIT` and friends.

## Tests

Run from the **repo root**:

```bash
mcp-server/.venv/Scripts/python -m pytest                 # everything (needs Docker for the integration tests)
mcp-server/.venv/Scripts/python -m pytest -m "not integration"   # unit tests only, no Docker
```

- **Unit tests** (`tests/mcp_server/unit`) use in-memory fakes. The database adapter is faked,
  so they check things like "a rejected query never reaches the database".
- **Integration tests** (`tests/mcp_server/integration`) start a real Postgres 16 container with the
  same init script and sample data as the dev setup, apply the real migration, and build the
  SQLite copy. Nearly every test runs against **both engines** and expects the same answer, and
  a set of them tries to write to the databases *with the service layer bypassed* to prove the
  database refuses on its own.

Checks used in CI: `ruff check`, `ruff format --check`, and `mypy` (strict) in this folder.

## Known limits

Things that are deliberately or currently not covered, so nobody is surprised later:

- **MySQL/MariaDB and SQL Server are not tested against live servers yet.** Their code paths
  follow each driver's documentation and are covered by URL-building and SQL-validation tests, but
  nothing has connected to a real one. SQL Server has no server-side query timeout or read-only
  session, so it relies on the database role and a client-side timeout; `explain_query` isn't
  available there.
- **Row cap works by reading a stream and stopping**, not by rewriting the SQL with `LIMIT`. This
  keeps `ORDER BY`, CTEs and duplicate column names working on every engine, but the database may
  still do the work of a large query until the time limit stops it.
- **Some valid SQL is refused.** Backslashes inside quoted text (engines disagree on what they mean),
  MySQL executable comments, and anything the parser can't understand are rejected rather than guessed at.
- **Catalog tables (`information_schema`, `pg_catalog`, `sqlite_master`) can't be queried**, since they'd
  list tables the caller isn't allowed to see. Use `list_tables` and `describe_table` instead.
- **Table names match case-insensitively.** If a database has two tables that differ only by case,
  a grant on one covers both.
- **`search_schema` finds tables and columns that have a row in `schema_descriptions`.** A description
  row can be empty; the schema editor (deliverable 8) will create rows for every table so all names
  are searchable.
- **`get_relationships` looks at each visible table in turn**, which is fine for typical schemas.
  The Redis cache planned for a later step is what keeps it cheap on very large ones.
- **Only the default schema is exposed**, plus any listed in a connection's `schemas` setting.
