"""known subjects, admin change log, and connection health columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21

Three additions the admin GUI needs:

  known_subjects   Users and roles that have been seen (by the MCP server when they make a
                   call, or by the GUI). The identity provider owns the real user list; this is
                   just enough for the permissions grid to have rows to show.
  admin_log        An append-only record of every change made through the GUI, so "who changed
                   this permission, and when?" always has an answer. The audit_log records what
                   AI callers did; this records what administrators did.
  connections      Result of the last "test connection" so the manager can show a status.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = [
    # ---- known subjects -------------------------------------------------------------------
    """
    CREATE TABLE known_subjects (
        subject_type   text        NOT NULL CHECK (subject_type IN ('user', 'role')),
        subject_id     text        NOT NULL CHECK (subject_id <> ''),
        display_name   text,
        first_seen_at  timestamptz NOT NULL DEFAULT now(),
        last_seen_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (subject_type, subject_id)
    )
    """,
    # ---- admin change log -----------------------------------------------------------------
    """
    CREATE TABLE admin_log (
        id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        occurred_at  timestamptz NOT NULL DEFAULT now(),
        actor_sub    text        NOT NULL,
        actor_name   text,
        action       text        NOT NULL,
        target_type  text        NOT NULL,
        target       text,
        details      jsonb       NOT NULL DEFAULT '{}'
    )
    """,
    "COMMENT ON COLUMN admin_log.details IS 'What changed. Never contains secrets.'",
    "CREATE INDEX idx_admin_log_occurred_at ON admin_log (occurred_at DESC)",
    "CREATE INDEX idx_admin_log_actor ON admin_log (actor_sub, occurred_at DESC)",
    # ---- connection health ----------------------------------------------------------------
    """
    ALTER TABLE connections
        ADD COLUMN last_checked_at   timestamptz,
        ADD COLUMN last_check_ok     boolean,
        ADD COLUMN last_check_error  text
    """,
    # `updated_at` is how the MCP server notices a connection was edited (and rebuilds its
    # connection pool and schema cache). Recording a health-check result must not count as an
    # edit, so the trigger now only fires when real configuration changed.
    "DROP TRIGGER connections_updated_at ON connections",
    """
    CREATE TRIGGER connections_updated_at BEFORE UPDATE ON connections
        FOR EACH ROW
        WHEN (
            (OLD.name, OLD.engine, OLD.description, OLD.details, OLD.secret_encrypted, OLD.is_active)
            IS DISTINCT FROM
            (NEW.name, NEW.engine, NEW.description, NEW.details, NEW.secret_encrypted, NEW.is_active)
        )
        EXECUTE FUNCTION set_updated_at()
    """,
    # ---- grants ---------------------------------------------------------------------------
    # The MCP server upserts the callers it sees (UPDATE is needed for ON CONFLICT ... DO UPDATE).
    "GRANT SELECT, INSERT, UPDATE ON known_subjects TO mcp_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON known_subjects TO gui_app",
    # Append-only for the GUI too: it can add entries and read them, never edit or delete.
    "GRANT SELECT, INSERT ON admin_log TO gui_app",
]

DOWNGRADE_STATEMENTS = [
    "DROP TRIGGER connections_updated_at ON connections",
    """
    CREATE TRIGGER connections_updated_at BEFORE UPDATE ON connections
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """,
    """
    ALTER TABLE connections
        DROP COLUMN last_check_error,
        DROP COLUMN last_check_ok,
        DROP COLUMN last_checked_at
    """,
    "DROP TABLE admin_log",
    "DROP TABLE known_subjects",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
