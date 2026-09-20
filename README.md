# mcp-server-for-sql-agents
A from-scratch MCP server that lets LLM agents query and explore SQL databases safely. Built to learn MCP internals; designed for reuse in Insight BI. For Open-Source Use

## What it will be

Two services that share one Postgres database:

- **MCP server**: lets an LLM explore and query registered SQL databases (Postgres, MySQL, SQL Server, SQLite)
  through tools like `list_tables`, `describe_table`, `search_schema` and `run_query`. Every call is checked
  against the caller's permissions and written to an audit log.
- **GUI manager**: an admin console for registering databases, deciding who can see which tables and use
  which tools, reading the audit log, and editing the schema descriptions the LLM sees.

## Status

Work in progress. Done so far:

- [x] Local Postgres + Redis, the `app_meta` schema, and sample data in Postgres and SQLite → see [db/README.md](db/README.md)
- [x] Database adapter and service layer (permissions, SQL validation, audit, output sanitizing), tested against Postgres and SQLite → see [mcp-server/README.md](mcp-server/README.md)

- [x] MCP server with all eight tools over stdio, ready for Claude Desktop, tested end to end against both engines

Next up: Streamable HTTP with OAuth 2.1, caching, then the GUI.

## Local development

```bash
cp .env.example .env    # fill in passwords
docker compose -f docker-compose.dev.yml --env-file .env up -d
```

The full walkthrough (migrations, sample data, database roles) is in [db/README.md](db/README.md).
