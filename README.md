# mcp-server-for-sql-agents

An MCP server that lets an LLM explore and query a company's SQL databases safely, plus an admin
console for deciding who can see what. It is the data layer for a larger "ask a question, get a chart"
app, and is built to be useful and deployable on its own.

Most MCP-and-SQL servers are one `run_query` tool wrapped around one connection string. This one is
built for the case where the database matters and several people share it:

| | |
|---|---|
| **More than one database engine** | Postgres, MySQL and SQLite are tested end to end; SQL Server goes through the same adapter (untested against a live server). A database is registered from the admin console, not in code: paste a connection string or fill in the fields, set driver options such as TLS, and the console says which engines this server can open. |
| **Schema the model can actually use** | `list_tables`, `describe_table`, `search_schema`, `get_relationships`, plus human-written descriptions that administrators edit and the model reads. |
| **Real access control** | Every person signs in. What they may see is granted per database, per table and per tool, and is checked on every call. There is no shared service account. |
| **Agents you approve** | An AI client that connects for the first time can do nothing until an administrator approves it. A pop-up appears in the console; you choose what it may do, on which databases, for how long. What an agent can do is the overlap of that and what the person using it may do. |
| **Works with online chatbots** | Add the address to Claude, ChatGPT, an editor or any client that speaks MCP over HTTP with OAuth. Clients register themselves, and the sign-in, consent and token checks follow the MCP authorization standards. |
| **Agents are told how to use it** | The server describes itself: instructions, a guide, SQL notes per engine, an error reference, prompts, and a tool where an agent can ask what it may do. |
| **An audit trail** | Every call is recorded, refused ones included, and so is every change an administrator makes. |
| **Limits enforced in the database too** | Read-only, row caps and timeouts are checked in the service layer and again by the database (a read-only role, or a read-only file for SQLite), never left to "the model should behave". |
| **Text from the database is treated as data** | Table comments, descriptions and cell values that read like instructions to an AI are withheld and flagged. |

## How it fits together

```
                 https://your-host
                        │
                 ┌──────┴──────┐
                 │    Caddy    │  TLS, one address for everything
                 └──────┬──────┘
   /              /api             /mcp, /.well-known         /auth
┌──────────┐  ┌────────────┐   ┌────────────────┐    ┌──────────────┐
│ gui-     │  │ gui-       │   │  mcp-server    │    │  Keycloak    │
│ frontend │  │ backend    │   │  (Streamable   │    │  (sign-in,   │
│ React    │  │ FastAPI    │   │   HTTP, OAuth  │    │   OAuth 2.1) │
│          │  │            │   │   2.1 resource │    │              │
└──────────┘  └─────┬──────┘   │   server)      │    └──────┬───────┘
                    │          └───────┬────────┘           │
                    │                  │ read-only          │
                    │        ┌─────────┴────────┐           │
                    │        │ your databases   │           │
                    │        │ (Postgres, SQLite│           │
                    │        │  MySQL, MSSQL)   │           │
                    │        └──────────────────┘           │
              ┌─────┴──────────────────────────────────────┴─┐
              │  Postgres: app_meta (connections, permissions, │
              │  descriptions, audit log, reports) + keycloak  │
              └───────────────────────┬───────────────────────┘
                                      │
                                    Redis  (caches; optional)
```

The MCP server and the admin API are separate services that share one Postgres database (`app_meta`)
and one identity provider, so a person has the same permissions whichever way they arrive. Neither
keeps state in memory, so both can run as several replicas behind a load balancer.

Inside the MCP server, tools are thin and the logic lives in a service layer that can be tested with no
network at all. Nothing above the `DBAdapter` interface knows which database engine it is talking to:
[mcp-server/README.md](mcp-server/README.md) explains the layers and what protects the data.

## Try it

You need Docker. The whole stack, with sample users and two sample databases:

```bash
cp .env.example .env
# edit .env: replace every "change-me". Generate the encryption key with
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Use letters and digits for passwords (they end up inside connection URLs).

docker compose --env-file .env --profile demo up -d --build
```

Give Keycloak a minute on first start, then open **https://localhost** (your browser will warn about the
certificate; Caddy made it for `localhost`). Sign in as `alice` (an administrator), `bob` (an analyst) or
`carol` (a viewer), with the password you set as `KEYCLOAK_DEV_USER_PASSWORD`.

| Address | What |
|---|---|
| `https://localhost/` | the admin console |
| `https://localhost/mcp` | the MCP server, for any client that speaks Streamable HTTP with OAuth 2.1 |
| `https://localhost/agent-guide` | the guide agents are given, readable by anyone |
| `https://localhost/auth` | Keycloak (its own admin console is not served here unless you ask; see below) |

Leave out `--profile demo` for a stack with no sample users or databases. That profile is for trying
things out; don't enable it anywhere real.

To reach it from another machine, set `PUBLIC_HOST` in `.env` to your hostname. Caddy then gets a public
certificate for it on its own, and the identity provider's setup step (it runs on every start) points the
console's redirect addresses and token audiences at the new host.

### Connecting an AI client

Open the console, go to **Connect**, and copy the server's address into a chatbot or tool: Claude,
ChatGPT, Claude Code, VS Code, Cursor, or anything that speaks MCP over HTTP. The steps for each are on that
screen and in [docs/connecting-agents.md](docs/connecting-agents.md). The first time an agent connects, the
console shows a pop-up asking whether to allow it, and with what limits.

To check that a chatbot really can connect to your deployment, run the same steps a chatbot would, with a
test account:

```bash
python deploy/verify_chatbot_flow.py https://your-host/mcp some-test-user 'their password'
```

### Working on it

Run only the infrastructure in Docker and the services on your machine:

```bash
docker compose -f docker-compose.dev.yml --env-file .env up -d    # Postgres, Redis, Keycloak
```

Then follow [db/README.md](db/README.md) for the database, [mcp-server/README.md](mcp-server/README.md)
and [gui-backend/README.md](gui-backend/README.md) for the services, and
[gui-frontend/README.md](gui-frontend/README.md) for the console.

## Tests

```bash
python -m pytest                            # from the repo root, with both Python packages installed; the integration tests start a real Postgres with Docker
cd gui-frontend && npm test                 # unit and component tests
cd gui-frontend && E2E_BASE_URL=https://localhost npm run e2e   # a real browser against the running stack
E2E_BASE_URL=https://localhost python -m pytest tests/e2e        # a chatbot connecting to the running stack
```

The Python tests cover the service layer with a mocked adapter, then the same behaviour against real
Postgres, MySQL and SQLite, the OAuth flow against a fake identity provider, the agent approval flow, and
the admin API over HTTP. The browser tests sign in through Keycloak's real login page, including the
pop-up for a new agent. The chatbot tests register a client, sign in with consent and PKCE, and talk MCP
with the token, and check that the things a chatbot must not be able to do are refused.

GitHub Actions runs lint (ruff), type checks (mypy and `tsc`), the tests, and builds the images.

## What's where

```
mcp-server/     the MCP server: adapters, services, tools, OAuth resource-server layer, cache
gui-backend/    the admin REST API (FastAPI)
gui-frontend/   the admin console (React, TypeScript, Vite)
db/             app_meta migrations (Alembic), sample data, Postgres init script
deploy/         Caddy config, Keycloak realm and setup script, setup image
docs/           connecting agents, security review, design notes for the console
tests/          Python tests for both services, and chatbot tests against a running stack
```

## Security notes

- **Read-only, twice.** The service layer accepts a single `SELECT` and rejects anything else (it parses
  the SQL, not just pattern-matches it). The database refuses writes independently: the Postgres role
  has `SELECT` only, and SQLite files are opened read-only.
- **`run_query` can't reach `app_meta`.** It connects only to registered target databases, and the
  database roles are set up so that even a bug couldn't change that.
- **Tokens are checked, not trusted.** Signature, expiry, issuer and audience are validated, so a token
  issued for another service is refused here. The identity provider is a separate service.
- **Agents are approved, not assumed.** A new AI client is refused everything until an administrator allows
  it, and an approval only ever narrows what the person using it may do. The MCP server's database role
  can't approve anything.
- **The endpoint checks who is knocking.** `Host` and `Origin` are validated on every request (DNS
  rebinding), bodies are capped, calls are rate limited per person, and self-registration of chatbots is
  limited to known sites, with PKCE required.
- **A database can only point where it should.** SQLite files must be inside one folder; cloud metadata
  addresses are refused as hosts.
- **Credentials go in and never come out.** Connection passwords are encrypted before they are stored,
  are never returned by any API, and never appear in logs or the admin log.
- **Nothing secret is in the repository.** `.env` is git-ignored and every image build ignores it;
  only `.env.example` with placeholder values is committed.

[docs/security.md](docs/security.md) goes through the standards this follows and how each was checked, what the
last review found and fixed, what is still open, and a checklist for running it for real.

## Roadmap

Deliberately not built yet:

- The natural-language orchestrator that turns a question into SQL and a chart. It is the reason this
  exists, and it will consume this server. The saved-reports table and API are its landing place.
- Queries across two registered databases at once. Each `run_query` targets exactly one connection.
- Warehouse engines that don't fit SQLAlchemy's async model, such as Snowflake and BigQuery. The
  `DBAdapter` interface is meant to take a new adapter class without touching the services or tools.
- Semantic (embedding) search over schema descriptions. `search_schema` uses Postgres full-text search and
  trigram matching today, and the place a vector index would slot in is marked in the code.
- Comparing report results over time, and anomaly detection on them.

MySQL is tested against a real server. SQL Server goes through the same adapter but hasn't been run against a
live one. The MySQL driver is in the images by default; to add SQL Server (which installs Microsoft's ODBC
driver too), set `DB_DRIVERS=mysql,mssql` in `.env` and rebuild.

## License

MIT. See [LICENSE](LICENSE).
