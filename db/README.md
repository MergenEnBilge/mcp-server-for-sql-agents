# Database setup

Everything in here builds the data layer for local development: one Postgres
server holding two databases, plus a SQLite copy of the sample data.

| Database   | What it is | Who uses it |
|------------|------------|-------------|
| `app_meta` | The app's own data: registered connections, permissions, schema descriptions, audit log, saved reports. **Always Postgres.** | mcp-server, gui-backend |
| `org_data` | A small online-shop database that stands in for "your company's data". The MCP server queries it like any other target. | mcp-server (read-only) |
| `sample.sqlite` | The same shop schema and data as `org_data`, as a SQLite file. Proves the server works across engines. | mcp-server (read-only) |

`run_query` only ever connects to a *target* database (`org_data`, the SQLite
file, or whatever an admin registers). It has no way to reach `app_meta`, and
the database roles below make sure of that even if the application code had a bug.

## Quick start

You need Docker and Python 3.12+.

```bash
# from the repo root
cp .env.example .env        # then replace every "change-me" with a real password
docker compose -f docker-compose.dev.yml --env-file .env up -d

cd db
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

alembic upgrade head                   # creates the app_meta tables
python sample_data/build_sqlite.py     # creates sample_data/sample.sqlite
```

Two things that trip people up:

- `APP_META_MIGRATIONS_URL` in `.env` has to use the same password you chose for
  `META_OWNER_PASSWORD`. If a password contains characters like `@` or `/`,
  URL-encode it in that one line.
- The init script only runs when the Postgres volume is empty. To start over from
  scratch: `docker compose -f docker-compose.dev.yml --env-file .env down -v`, then `up -d` again.

## Database roles

Nothing connects as the Postgres superuser except the one-time init script.
Each part of the system gets its own role with only the permissions it needs.
The roles are created in [`init/01-create-roles-and-databases.sh`](init/01-create-roles-and-databases.sh);
table-level grants on `app_meta` are made by the migration.

| Role           | Can do | Cannot do |
|----------------|--------|-----------|
| `meta_owner`   | Owns `app_meta`; runs migrations | – |
| `mcp_app`      | Read every config table; append rows to `audit_log` | Change config, read or edit the audit log, connect to `org_data` |
| `gui_app`      | Full control of config tables; read `audit_log` | Write, edit or delete audit rows, connect to `org_data` |
| `org_owner`    | Owns the sample `org_data` (dev only) | – |
| `org_readonly` | `SELECT` on `org_data`, nothing else | Write, create tables (even temp ones), connect to `app_meta` |

`org_readonly` is protected in layers, and the layers are independent:

1. **Privileges.** It has `SELECT` and no other grant, so every `INSERT`, `UPDATE`,
   `DELETE`, `TRUNCATE`, `DROP` and `CREATE` is refused by Postgres itself.
2. **Read-only sessions by default** (`default_transaction_read_only`). A second
   line of defense, and it turns writes into a clear error message.
3. **A 10-second statement timeout** set on the role. The service layer applies a
   tighter per-request timeout (5s by default); this is the ceiling if that layer ever fails.

The audit log is append-only for both services. Cleaning up old rows is an
operator job done as `meta_owner`, not something either service can do.

## What's in `app_meta`

Defined in [`migrations/versions/0001_app_meta_schema.py`](migrations/versions/0001_app_meta_schema.py).
Column-level notes live in the database itself (`\d+ connections` in psql).

- **`connections`**: the registry of target databases. Non-secret settings (host, port, database name)
  go in a JSON `details` column. The password is stored Fernet-encrypted in `secret_encrypted`, with the
  key kept in the service environment, never in this database.
- **`connection_access`**, **`table_permissions`**, **`tool_permissions`**: the three layers of
  authorization. A caller may use a connection, see specific tables on it, and call specific
  tools. Grants can be made to a `user` (the identity provider's subject id) or a `role`.
  **Default deny**: a row is a grant, and no row means no access.
- **`schema_descriptions`**: human-written descriptions of tables and columns, which the LLM reads through
  `describe_table` and `search_schema`. It has a full-text index and trigram indexes, so
  `search_schema` works the same whichever engine the target database is.
  A row with an empty description is valid; it still makes that table or column searchable.
- **`audit_log`**: one row per tool call: who, which tool, which connection and tables, the full
  arguments, timing, and the outcome. `connection_name` is plain text (not a foreign key) so the history
  survives a connection being renamed or deleted.
- **`saved_reports`**: a stub for the future BI app. Deleting a connection that still has reports is refused.

## Sample data

[`sample_data/schema.sql`](sample_data/schema.sql) is one portable SQL file loaded into both
Postgres and SQLite, so the two engines hold the same seven tables. How they relate:

- an `order` belongs to a `customer`, and has many `order_items` and `payments`
- an `order_item` points at a `product`; a `product` belongs to a `category`
- `categories` reference themselves (`parent_id`), so Headphones sits under Electronics
- a `review` links a `customer` to a `product`

[`sample_data/seed_data.sql`](sample_data/seed_data.sql) has 25 customers, 70 orders, 177 order items,
64 payments and 32 reviews, generated once with a fixed seed. Each captured payment equals the total of its
order's items. Both engines end up with identical rows.

One real difference between the engines: SQLite has no exact decimal type and stores `NUMERIC` as floating
point, so summing prices there can give `96.78999999999999` where Postgres gives `96.79`. The stored values
are the same; only the arithmetic differs. Keep it in mind when comparing results across engines.

The last two rows in `reviews` contain text that pretends to be instructions to an AI ("ignore all previous
instructions…", a fake tool call). That's on purpose. It's realistic customer-written content, and
it lets us test that anything read from the database is treated strictly as data.

## Checking your setup

```bash
# read-only role can read...
docker exec -e PGPASSWORD=<ORG_READONLY_PASSWORD> mcp-sql-dev-postgres-1 \
  psql -h localhost -U org_readonly -d org_data -c "select count(*) from orders"      # 70

# ...but not write, and can't even connect to app_meta
docker exec -e PGPASSWORD=<ORG_READONLY_PASSWORD> mcp-sql-dev-postgres-1 \
  psql -h localhost -U org_readonly -d org_data -c "delete from orders"               # permission denied
docker exec -e PGPASSWORD=<ORG_READONLY_PASSWORD> mcp-sql-dev-postgres-1 \
  psql -h localhost -U org_readonly -d app_meta -c "select 1"                         # no CONNECT privilege
```

## Changing the schema

Add a new file under `migrations/versions/` (copy the header from `0001`, bump `revision` and
set `down_revision` to the previous one). Give any new table explicit `GRANT`s to `mcp_app` and `gui_app`;
nothing is granted automatically. Always write a working `downgrade()`.
