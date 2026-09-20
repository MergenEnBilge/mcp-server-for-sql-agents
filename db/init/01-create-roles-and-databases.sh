#!/bin/bash
# Runs once, the first time the Postgres container starts with an empty data volume.
#
# Sets up two databases on one server and five least-privilege roles:
#
#   app_meta  (owner meta_owner)  the app's own data: connections, permissions, audit log
#   org_data  (owner org_owner)   the sample "customer" database the MCP server queries
#
#   meta_owner     runs Alembic migrations against app_meta
#   mcp_app        mcp-server: read config, append audit rows
#   gui_app        gui-backend: manage config, read audit rows
#   org_owner      loads the sample data (dev only; a real target DB already exists)
#   org_readonly   what run_query connects as: SELECT only, on org_data only
#
# Table-level grants on app_meta are made by the Alembic migrations, not here.
set -euo pipefail

PSQL=(psql -v ON_ERROR_STOP=1)

# Passwords are passed as psql variables and quoted with :'name', so odd
# characters in a password can't break (or inject into) the statements.
"${PSQL[@]}" --username "$POSTGRES_USER" --dbname postgres \
  -v meta_owner_pw="$META_OWNER_PASSWORD" \
  -v mcp_app_pw="$MCP_APP_PASSWORD" \
  -v gui_app_pw="$GUI_APP_PASSWORD" \
  -v org_owner_pw="$ORG_OWNER_PASSWORD" \
  -v org_readonly_pw="$ORG_READONLY_PASSWORD" <<'SQL'
CREATE ROLE meta_owner   LOGIN PASSWORD :'meta_owner_pw';
CREATE ROLE mcp_app      LOGIN PASSWORD :'mcp_app_pw';
CREATE ROLE gui_app      LOGIN PASSWORD :'gui_app_pw';
CREATE ROLE org_owner    LOGIN PASSWORD :'org_owner_pw';
CREATE ROLE org_readonly LOGIN PASSWORD :'org_readonly_pw';

CREATE DATABASE app_meta OWNER meta_owner;
CREATE DATABASE org_data OWNER org_owner;

-- Postgres lets every role connect to every database by default. Close that,
-- then open exactly the doors we want. Most importantly: org_readonly must not
-- be able to reach app_meta at all.
REVOKE CONNECT, TEMPORARY ON DATABASE app_meta FROM PUBLIC;
REVOKE CONNECT, TEMPORARY ON DATABASE org_data FROM PUBLIC;
GRANT CONNECT ON DATABASE app_meta TO mcp_app, gui_app;
GRANT CONNECT ON DATABASE org_data TO org_readonly;

-- Defaults for every session org_readonly opens. The real protection is that
-- the role only has SELECT (granted below); these are belt-and-braces plus a
-- hard ceiling on how long any one statement can run. The service layer sets
-- a tighter per-request timeout on top of this.
ALTER ROLE org_readonly SET default_transaction_read_only = on;
ALTER ROLE org_readonly SET statement_timeout = '10s';
ALTER ROLE org_readonly SET idle_in_transaction_session_timeout = '15s';
SQL

# Sample data is loaded as org_owner so that org_owner (not the superuser) owns
# the tables. Local socket connections are trusted during first-run init.
for file in schema.sql seed_data.sql; do
  "${PSQL[@]}" --username org_owner --dbname org_data --file "/sample_data/$file"
done

"${PSQL[@]}" --username org_owner --dbname org_data <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO org_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO org_readonly;
-- Tables added to org_data later are readable too.
ALTER DEFAULT PRIVILEGES FOR ROLE org_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO org_readonly;
SQL
