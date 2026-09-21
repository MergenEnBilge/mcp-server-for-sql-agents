# gui-backend

The REST API behind the admin console. It is where administrators decide **who may use which
database, table and tool**, look back over **what AI callers have been doing**, and (later
screens) edit the schema descriptions the model reads and watch the system's health.

It doesn't talk to the target databases on behalf of AI callers; that is the MCP server's job.
The two share one Postgres (`app_meta`) and, through it, one set of rules.

## Running it

```bash
pip install -e ../mcp-server -e ".[dev]"      # it reuses the MCP server's crypto, cache and adapters
uvicorn gui_backend.main:app_factory --factory --port 8100
```

Settings come from `GUI_`-prefixed environment variables (see `.env.example`):

| Variable | Meaning |
|---|---|
| `GUI_APP_META_URL` | app_meta database, as the `gui_app` role |
| `GUI_CONNECTION_SECRET_KEYS` | the same Fernet keys as the MCP server (this service encrypts, that one decrypts) |
| `GUI_OAUTH_ISSUER`, `GUI_OAUTH_AUDIENCE` | identity provider, and the audience tokens must carry to be accepted here |
| `GUI_ADMIN_ROLE` | role (or scope) that grants admin access. Default `admin` |
| `GUI_REDIS_URL` | optional; lets a change made here invalidate the MCP server's caches instantly |
| `GUI_CORS_ORIGINS` | only needed in development, when the UI runs on its own port |

## How sign-in works

The browser app signs the person in with the identity provider (authorization code flow with PKCE)
and sends the access token as a Bearer token. This API only *checks* it: signature, expiry, issuer,
and that it was issued **for this API** (`GUI_OAUTH_AUDIENCE`), so a token minted for the MCP server
can't be used here even though both trust the same provider. Nothing about a session lives on the
server, so any number of replicas can run side by side.

Every endpoint needs a valid token. Endpoints for managing things need the `admin` role (or scope).
A signed-in person who isn't an administrator can only ask `/api/me`.

## What it offers

| Area | Endpoints |
|---|---|
| Session | `GET /api/me` |
| Audit log | `GET /api/audit` (filter by user, tool, connection, table, outcome, date, text; sort; page), `GET /api/audit/{id}`, `GET /api/audit/facets`, `GET /api/admin-log` |
| Connections | `GET/POST /api/connections`, `GET/PUT/DELETE /api/connections/{id}`, `POST /api/connections/{id}/test`, `POST /api/connections/test`, `GET/PUT /api/connections/{id}/access`, `GET /api/engines` |
| Permissions | `GET/POST /api/permissions/subjects`, `GET/PUT /api/permissions/tables`, `GET/PUT /api/permissions/tools` |
| Schema descriptions | `GET /api/schema/tables`, `GET /api/schema/table`, `PUT/DELETE /api/schema/description` |
| Ops | `GET /healthz` (process is up), `GET /readyz` (database reachable) |

## Guarantees worth knowing about

- **Credentials go in and never come out.** A connection's password is encrypted before it is stored,
  responses only say whether one exists, and it never appears in the admin log. A secret can't be
  hidden in the plain-text settings either: names like `password` or `token` are refused there.
- **Every change is recorded** in `admin_log` (who, what, when), in the same transaction as the change.
  The log is append-only: this service's database role can add and read entries, never edit or delete.
- **Changes take effect immediately.** After committing, the API bumps a version counter in Redis that
  the MCP server includes in its cache keys, so a revoked permission doesn't wait for a cache to expire.
- **Descriptions are checked for prompt injection as they are written.** Saving text that reads like an
  instruction to an AI ("ignore your previous instructions...") is allowed but comes back with the rule
  it tripped, because the MCP server withholds such text and the editor should say so.
- **A health check isn't an edit.** Testing a connection records the result without touching the
  connection's `updated_at`, which the MCP server watches to know when to rebuild its connection pool.
- **Failures say what to fix.** "Connection failed: could not reach host db on port 5432." rather than a
  driver stack trace, and never with the password in it.

## Tests

From the repo root: `mcp-server/.venv/Scripts/python -m pytest tests/gui_backend`. They start a real
Postgres with the real migrations and call the API over HTTP. One test changes a permission through
the API and checks the MCP server's services obey it on the very next request.
