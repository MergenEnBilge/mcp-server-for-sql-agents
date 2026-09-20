"""app_meta schema: connections, permissions, schema descriptions, audit log, saved reports

Revision ID: 0001
Revises:
Create Date: 2026-09-21

Everything the MCP server and GUI manager store about themselves. Nothing here
holds the organisation's business data; that lives in the target databases
registered in `connections`.

Who touches what (grants at the bottom of this file):
  mcp_app  reads all config tables, appends to audit_log, nothing else
  gui_app  full control of config tables, read-only on audit_log

The audit log is append-only for both services: neither role is granted
UPDATE or DELETE on it.

Roles are created by db/init/01-create-roles-and-databases.sh, which must have
run before this migration.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# asyncpg cannot run several statements in one call, so each one is its own list item.
UPGRADE_STATEMENTS = [
    # pg_trgm powers fuzzy matching on table/column names in search_schema.
    # Trusted extension: the database owner may create it without superuser rights.
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    # Keeps updated_at honest no matter which service wrote the row.
    """
    CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END
    $$
    """,
    # ---- connections: the registry of target databases ------------------------------------
    """
    CREATE TABLE connections (
        id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
        name              text        NOT NULL UNIQUE
                              CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
        engine            text        NOT NULL,
        description       text        NOT NULL DEFAULT '',
        details           jsonb       NOT NULL DEFAULT '{}',
        secret_encrypted  text,
        is_active         boolean     NOT NULL DEFAULT true,
        created_by        text,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
    (
        "COMMENT ON COLUMN connections.name IS "
        "'Short identifier the LLM passes as connection_name. Lower-case letters, digits, - and _.'"
    ),
    (
        "COMMENT ON COLUMN connections.engine IS "
        "'SQLAlchemy dialect name, e.g. postgresql, mysql, mssql, sqlite. Validated in application code so new engines need no migration.'"
    ),
    (
        "COMMENT ON COLUMN connections.details IS "
        "'Non-secret connection settings (host, port, database, username, driver options). Never put passwords here.'"
    ),
    (
        "COMMENT ON COLUMN connections.secret_encrypted IS "
        "'Fernet-encrypted password/secret. The key lives in the service environment, not in this database. Never returned by any API.'"
    ),
    """
    CREATE TRIGGER connections_updated_at BEFORE UPDATE ON connections
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """,
    # ---- access control -----------------------------------------------------------------
    # Default deny: a row here is a grant. No row means no access.
    # subject_id is the identity provider's user id (subject claim) for 'user', or the role name for 'role'.
    """
    CREATE TABLE connection_access (
        connection_id  uuid        NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
        subject_type   text        NOT NULL CHECK (subject_type IN ('user', 'role')),
        subject_id     text        NOT NULL CHECK (subject_id <> ''),
        granted_by     text,
        granted_at     timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (connection_id, subject_type, subject_id)
    )
    """,
    (
        "COMMENT ON TABLE connection_access IS "
        "'Who may use a connection at all. A caller also needs table_permissions to see any table on it.'"
    ),
    """
    CREATE TABLE table_permissions (
        connection_id  uuid        NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
        subject_type   text        NOT NULL CHECK (subject_type IN ('user', 'role')),
        subject_id     text        NOT NULL CHECK (subject_id <> ''),
        table_name     text        NOT NULL CHECK (table_name <> ''),
        granted_by     text,
        granted_at     timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (connection_id, subject_type, subject_id, table_name)
    )
    """,
    (
        "COMMENT ON COLUMN table_permissions.table_name IS "
        "'Table name as the target database reports it; tables outside the default schema are written schema.table.'"
    ),
    """
    CREATE TABLE tool_permissions (
        subject_type  text        NOT NULL CHECK (subject_type IN ('user', 'role')),
        subject_id    text        NOT NULL CHECK (subject_id <> ''),
        tool_name     text        NOT NULL CHECK (tool_name <> ''),
        granted_by    text,
        granted_at    timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (subject_type, subject_id, tool_name)
    )
    """,
    (
        "COMMENT ON TABLE tool_permissions IS "
        "'Which MCP tools a user or role may call (list_tables, run_query, ...). Applies across all connections.'"
    ),
    # ---- schema descriptions: the curated layer the LLM reads ------------------------------
    # column_name IS NULL means the row describes the table itself.
    # A row with an empty description is fine: it still makes the name searchable.
    """
    CREATE TABLE schema_descriptions (
        id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        connection_id  uuid        NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
        table_name     text        NOT NULL CHECK (table_name <> ''),
        column_name    text        CHECK (column_name <> ''),
        description    text        NOT NULL DEFAULT '',
        updated_by     text,
        created_at     timestamptz NOT NULL DEFAULT now(),
        updated_at     timestamptz NOT NULL DEFAULT now(),
        search_vector  tsvector    GENERATED ALWAYS AS (
            to_tsvector('english', table_name || ' ' || coalesce(column_name, '') || ' ' || description)
        ) STORED,
        UNIQUE NULLS NOT DISTINCT (connection_id, table_name, column_name)
    )
    """,
    # search_schema v1 = full-text search on search_vector + trigram similarity on names.
    # A vector-embedding column and index would sit alongside these for semantic search later.
    "CREATE INDEX idx_schema_descriptions_search ON schema_descriptions USING gin (search_vector)",
    "CREATE INDEX idx_schema_descriptions_table_trgm ON schema_descriptions USING gin (table_name gin_trgm_ops)",
    "CREATE INDEX idx_schema_descriptions_column_trgm ON schema_descriptions USING gin (column_name gin_trgm_ops)",
    """
    CREATE TRIGGER schema_descriptions_updated_at BEFORE UPDATE ON schema_descriptions
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """,
    # ---- audit log ------------------------------------------------------------------------
    # connection_name is stored as text rather than a foreign key so history survives
    # a connection being deleted or renamed.
    """
    CREATE TABLE audit_log (
        id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        occurred_at      timestamptz NOT NULL DEFAULT now(),
        caller_sub       text        NOT NULL,
        caller_name      text,
        tool_name        text        NOT NULL,
        connection_name  text,
        tables           text[]      NOT NULL DEFAULT '{}',
        arguments        jsonb       NOT NULL DEFAULT '{}',
        success          boolean     NOT NULL,
        error_message    text,
        row_count        integer,
        result_summary   text,
        duration_ms      integer     NOT NULL CHECK (duration_ms >= 0)
    )
    """,
    "COMMENT ON COLUMN audit_log.caller_sub IS 'Subject claim from the validated OAuth token.'",
    "COMMENT ON COLUMN audit_log.tables IS 'Tables the call touched, so the audit viewer can filter by table.'",
    "CREATE INDEX idx_audit_log_occurred_at ON audit_log (occurred_at DESC)",
    "CREATE INDEX idx_audit_log_caller ON audit_log (caller_sub, occurred_at DESC)",
    "CREATE INDEX idx_audit_log_tool ON audit_log (tool_name, occurred_at DESC)",
    "CREATE INDEX idx_audit_log_connection ON audit_log (connection_name, occurred_at DESC)",
    "CREATE INDEX idx_audit_log_tables ON audit_log USING gin (tables)",
    # ---- saved reports (stub for the future BI app) -----------------------------------------
    # RESTRICT: deleting a connection that still has reports is refused rather than silently
    # taking the reports with it.
    """
    CREATE TABLE saved_reports (
        id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
        name           text        NOT NULL CHECK (name <> ''),
        description    text        NOT NULL DEFAULT '',
        connection_id  uuid        NOT NULL REFERENCES connections (id) ON DELETE RESTRICT,
        sql            text        NOT NULL,
        chart_config   jsonb       NOT NULL DEFAULT '{}',
        owner_sub      text        NOT NULL,
        created_at     timestamptz NOT NULL DEFAULT now(),
        updated_at     timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_saved_reports_owner ON saved_reports (owner_sub)",
    """
    CREATE TRIGGER saved_reports_updated_at BEFORE UPDATE ON saved_reports
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """,
    # ---- grants ---------------------------------------------------------------------------
    """
    GRANT SELECT ON connections, connection_access, table_permissions, tool_permissions,
                    schema_descriptions, saved_reports TO mcp_app
    """,
    "GRANT INSERT ON audit_log TO mcp_app",
    """
    GRANT SELECT, INSERT, UPDATE, DELETE ON connections, connection_access, table_permissions,
                    tool_permissions, schema_descriptions, saved_reports TO gui_app
    """,
    "GRANT SELECT ON audit_log TO gui_app",
]

DOWNGRADE_STATEMENTS = [
    "DROP TABLE saved_reports",
    "DROP TABLE audit_log",
    "DROP TABLE schema_descriptions",
    "DROP TABLE tool_permissions",
    "DROP TABLE table_permissions",
    "DROP TABLE connection_access",
    "DROP TABLE connections",
    "DROP FUNCTION set_updated_at()",
    # pg_trgm is left installed: something else in the database may rely on it.
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
