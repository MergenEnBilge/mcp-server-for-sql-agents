# mcp-server

The part of the project that sits between an LLM and your databases: an MCP server with eight
read-only data tools and one that tells an agent what it may do. The real logic lives in a **service
layer** that can be used and tested with no MCP and no network involved; the MCP tools are a thin
wrapper over it.

## How it's layered

```
   tools/ (MCP tools)                 <- thin: translate MCP calls into service calls
            │
   services/                          <- all the real logic: who may do what, is this SQL safe,
   ├─ permission_service (is this AI client approved? may this person do it?)
   │                                     what came back, what to write in the audit log
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

   auth/                              <- who is calling: token checks, and noticing new AI clients
   docs/                              <- the guide agents read (Markdown, shipped in the package)
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
| Which AI clients may connect | A client is refused everything until an administrator approves it, and what it may do is capped by that approval as well as by the person it acts for | The MCP server's database role can add a *pending* client and refresh who and when; it has no right to write the columns that approve one |
| Fair use | Calls per minute and calls at once, per person (shared through Redis when it is there) | – |
| Where a database may point | SQLite files only inside `MCP_SQLITE_ROOT`; link-local and metadata addresses refused as hosts | – |
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

## Connecting Claude Desktop (stdio)

stdio has no login screen, so the identity the server acts as is set in its environment. There is
deliberately no default: without `MCP_STDIO_SUB` the server refuses to start.

Add this to Claude Desktop's `claude_desktop_config.json` (adjust the paths and values; the
database URL and key are the ones from your `.env`):

```json
{
  "mcpServers": {
    "sql-data-layer": {
      "command": "D:/path/to/repo/mcp-server/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_sql_server"],
      "env": {
        "MCP_APP_META_URL": "postgresql+asyncpg://mcp_app:<password>@localhost:5432/app_meta",
        "MCP_CONNECTION_SECRET_KEYS": "<your fernet key>",
        "MCP_STDIO_SUB": "dev-analyst",
        "MCP_STDIO_NAME": "Dev Analyst",
        "MCP_STDIO_ROLES": "analyst"
      }
    }
  }
}
```

With the sample data seeded (`python -m mcp_sql_server.devtools.seed`), try asking: *"Which product
categories have the most products? Use the shop-sqlite database."*

The tools are `list_connections`, `list_tables`, `describe_table`, `search_schema`,
`get_relationships`, `run_query`, `explain_query` and `get_sample_rows`, plus `get_my_access`. All of
them are marked read-only, and the server's instructions tell the model that text coming out of a
database is data, never instructions.

stdio has no OAuth client, so it has no agent to approve: whoever writes the configuration is trusted
to decide who the server acts as.

## What agents are told

Agents shouldn't have to guess how to use this. The server tells them in four ways:

- **Instructions**, sent when a client connects: the order of work, the limits, that database text is
  data, and to call `get_my_access` when unsure.
- **`get_my_access`**, a tool that is always allowed. It says whether the agent is approved, which
  tools and databases it has right now, and what to tell the person if something is missing. An agent
  that hasn't been approved yet can still call it, which is how it finds out why it is being refused.
- **Resources** (`sql-data-layer://guide`, `://dialects`, `://errors`): a short guide, SQL notes per
  engine, and what each error message means. The text is in [mcp_sql_server/docs](mcp_sql_server/docs);
  a test keeps it in step with the code.
- **Prompts** (`explore_database`, `answer_data_question`, `check_my_access`), which clients that list
  prompts show as shortcuts.

The guide is also served without signing in at `/agent-guide`, so it can be read before connecting.

## Which AI clients may connect

Over HTTP, the OAuth client in the token (its `azp` claim) identifies the AI client, and the identity
provider sets it, so the agent can't choose it. A client seen for the first time is recorded as
`pending` and every tool refuses it with a message that tells the model to ask the user to have an
administrator approve it. The admin console shows the request as a pop-up (see
[gui-frontend](../gui-frontend/README.md)); the administrator allows it with a ceiling of tools, databases
and a duration, or blocks it.

What an agent can do is the overlap of two things: what it was approved for, and what the signed-in
person is allowed. It can never exceed either. The client is noticed as soon as it introduces itself
(an `initialize` request, or the client info that newer protocol revisions send with every request),
so the request appears before the agent has tried anything. The name it gives is shown to the
administrator as a hint only.

Approvals are checked on every call, cached for a minute, and the cache is dropped the moment the
console changes one, so blocking an agent takes effect straight away.

## Serving over HTTP with OAuth 2.1

For anything shared, run the Streamable HTTP transport. It is an OAuth 2.1 **resource server**: it
never issues tokens, it only checks the ones it is handed, and the identity provider (Keycloak in the
compose file) is a separate service.

```bash
python -m mcp_sql_server --transport http
```

What it does:

- A request with no token gets `401` and a `WWW-Authenticate` header pointing at
  `/.well-known/oauth-protected-resource`, which names the authorization server. MCP clients follow
  that to log the user in (authorization code flow with PKCE, which the identity provider enforces).
- Tokens are checked locally against the provider's published signing keys: signature, expiry,
  issuer, and **audience**. A token minted for some other service is refused, even if it is otherwise
  valid, so it can't be replayed here. Only asymmetric algorithms are accepted (no `none`, no `HS*`).
  If the provider can't be reached, new tokens are refused rather than waved through.
- The token's subject and roles become the caller the services authorize and audit, so a person has
  the same permissions here as in the admin GUI. Tool arguments can't change who the caller is.
- The `Host` and `Origin` headers of every request are checked against the server's own address
  (DNS-rebinding protection, which the MCP specification requires). Request bodies are capped at
  256 KB and responses are marked `no-store`.
- Clients that don't have a client set up in advance (online chatbots) register themselves through the
  identity provider's dynamic client registration; see [docs/connecting-agents.md](../docs/connecting-agents.md).
- It is **stateless**: no session lives in the process, so replicas can sit behind a load balancer
  with no sticky sessions. `/healthz` is public and only says the process is up.

## Running in Docker

`mcp-server/Dockerfile` builds an image that serves the HTTP transport as a non-root user. Build it from
the repository root (it is one of the services in `docker-compose.yml`):

```bash
docker build -f mcp-server/Dockerfile -t mcp-sql-server .
```

The MySQL/MariaDB driver is included. To build a different set of drivers, pass `--build-arg EXTRAS=...`
(for example `EXTRAS=mysql,mssql`, or `DB_DRIVERS=mysql,mssql` in `.env` for the compose file). Asking for
`mssql` also installs Microsoft's ODBC driver into the image. The admin console shows which engines the
running server can open, so a database of an engine it can't open is never registered by mistake.

Settings (all `MCP_`-prefixed environment variables):

| Variable | Meaning |
|---|---|
| `PUBLIC_URL` | The address clients use, e.g. `https://mcp.example.com/mcp`. This is the resource identifier. |
| `OAUTH_ISSUER` | The identity provider's issuer URL. |
| `OAUTH_AUDIENCE` | What the token's `aud` must contain. Defaults to `PUBLIC_URL`. |
| `OAUTH_JWKS_URL` | Where the signing keys are. Found through OpenID discovery if not set. |
| `OAUTH_ROLES_CLAIM` | Dotted path to the roles in the token. Default `realm_access.roles` (Keycloak). |
| `OAUTH_REQUIRED_SCOPES` | Comma-separated scopes every token must carry (optional). |
| `HTTP_HOST`, `HTTP_PORT` | Where to listen. Default `127.0.0.1:8000`. |
| `ALLOWED_HOSTS`, `ALLOWED_ORIGINS` | Extra `Host` and `Origin` values to accept, comma-separated, besides the server's own address. Origins are only needed for MCP clients that run inside a web page. |
| `RATE_LIMIT_PER_MINUTE`, `MAX_CONCURRENT_CALLS` | Fair use per signed-in person. Defaults 120 and 4; 0 turns a limit off. |
| `SQLITE_ROOT` | SQLite databases must be inside this folder. Without it, SQLite connections are refused. |

## Caching (Redis)

Set `MCP_REDIS_URL` and the server caches the lookups that happen on every request. Without it,
everything still works, just without the speed-up. The cache is an optimisation and is treated as
one: if Redis is down or slow, requests take the normal path (a short timeout keeps a Redis outage
from becoming an application outage). Authentication is the opposite and fails closed.

| What | Cached for | How it's keyed |
|---|---|---|
| Which connections, tables and tools a caller may use | 60 s | the caller's user id plus roles |
| Table and column descriptions | 5 min | the connection |
| Table structure of the target databases (`list_tables`, `describe_table`) | 5 min | the connection and its last edit |
| Token introspection answers (only in introspection mode) | 60 s, never past the token's expiry | a hash of the token |

Things that make this safe to leave on:

- **No leakage between callers.** Permission answers are keyed by the caller's exact set of grants,
  and table structure is cached *raw* (the same for everyone) with each caller's permissions applied
  afterwards on every request. Nothing about who asked ever goes into a cached structure.
- **Changes are effective immediately.** Every key carries a version number. The admin GUI bumps it
  after any permission, connection or description change, which makes all old entries unreachable
  at once, so revoking access doesn't wait for a TTL.
- **Secrets and data stay out.** Connection secrets, query results and the audit write never go
  through the cache, and tokens are only ever stored as a hash.

## Tests

Run from the **repo root**:

```bash
mcp-server/.venv/Scripts/python -m pytest                 # everything (needs Docker for the integration tests)
mcp-server/.venv/Scripts/python -m pytest -m "not integration"   # unit tests only, no Docker
```

- **Unit tests** (`tests/mcp_server/unit`) use in-memory fakes. The database adapter is faked,
  so they check things like "a rejected query never reaches the database".
- **MySQL** has its own integration test (`test_mysql.py`) against a real MySQL 8.4 container: schema
  reflection, the row cap, the time limit, the read-only session (even for a database user who could
  write), the validator's MySQL rules and the console's connection check. It needs
  `pip install -e ".[mysql]"` and is skipped without it.
- **Integration tests** (`tests/mcp_server/integration`) start a real Postgres 16 container with the
  same init script and sample data as the dev setup, apply the real migration, and build the
  SQLite copy. Nearly every test runs against **both engines** and expects the same answer, and
  a set of them tries to write to the databases *with the service layer bypassed* to prove the
  database refuses on its own.

Checks used in CI: `ruff check`, `ruff format --check`, and `mypy` (strict) in this folder.

## Known limits

Things that are deliberately or currently not covered, so nobody is surprised later:

- **SQL Server is not tested against a live server.** MySQL is (see above), and SQL Server's ODBC
  driver is checked to load in the image, but nothing has run queries against a real SQL Server. It
  has no server-side query timeout or read-only session, so it relies on the database role and a
  client-side timeout; `explain_query` isn't available there.
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
