"""agents: which AI clients may use the server, and what an administrator allowed them

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21

Until now the server only knew *people*. But a person reaches it through an AI client (a chatbot,
an IDE, a script), and the same person may use several. Each of those is an "agent", identified
by the OAuth client it signed in with (the token's `azp` claim).

  agents             One row per client the server has seen. A client that shows up for the first
                     time is created here as `pending` and can do nothing until an administrator
                     approves it in the console. Approval records the ceiling for that agent: the
                     tools it may call, and which databases. What it may actually do is the
                     overlap of that ceiling and what the signed-in person is allowed (so an agent
                     can never do more than the person behind it).
  agent_connections  The databases an approved agent is limited to (when it isn't allowed "all").

The MCP server's database role can add a row (always `pending`) and update who/when it was last
seen, and nothing else: it can never approve an agent. Only the admin API can.

`audit_log.client_id` records which agent made each call.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = [
    """
    CREATE TABLE agents (
        id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
        client_id        text        NOT NULL UNIQUE CHECK (client_id <> ''),
        label            text        NOT NULL DEFAULT '',
        reported_name    text,
        status           text        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'blocked')),
        allowed_tools    text[]      NOT NULL DEFAULT '{}',
        all_connections  boolean     NOT NULL DEFAULT true,
        expires_at       timestamptz,
        first_seen_at    timestamptz NOT NULL DEFAULT now(),
        last_seen_at     timestamptz NOT NULL DEFAULT now(),
        last_user_sub    text,
        last_user_name   text,
        decided_by       text,
        decided_at       timestamptz
    )
    """,
    (
        "COMMENT ON COLUMN agents.client_id IS "
        "'The OAuth client id from the access token (azp). Set by the identity provider, not by the agent.'"
    ),
    (
        "COMMENT ON COLUMN agents.reported_name IS "
        "'What the agent called itself when it connected (MCP clientInfo). Not verified: shown to administrators as a hint only.'"
    ),
    (
        "COMMENT ON COLUMN agents.allowed_tools IS "
        "'Ceiling: tools this agent may call. What it can actually do is this AND the signed-in person''s own grants.'"
    ),
    "CREATE INDEX idx_agents_status ON agents (status, first_seen_at)",
    """
    CREATE TABLE agent_connections (
        agent_id       uuid NOT NULL REFERENCES agents (id) ON DELETE CASCADE,
        connection_id  uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
        PRIMARY KEY (agent_id, connection_id)
    )
    """,
    "ALTER TABLE audit_log ADD COLUMN client_id text",
    "CREATE INDEX idx_audit_log_client ON audit_log (client_id, occurred_at DESC)",
    # ---- grants ---------------------------------------------------------------------------
    "GRANT SELECT ON agents, agent_connections TO mcp_app",
    # Column-level on purpose: a new row can only ever be `pending` with no tools (the column
    # defaults), and the server can only refresh who and when.
    "GRANT INSERT (client_id, reported_name, last_user_sub, last_user_name) ON agents TO mcp_app",
    "GRANT UPDATE (reported_name, last_seen_at, last_user_sub, last_user_name) ON agents TO mcp_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON agents, agent_connections TO gui_app",
]

DOWNGRADE_STATEMENTS = [
    "DROP INDEX idx_audit_log_client",
    "ALTER TABLE audit_log DROP COLUMN client_id",
    "DROP TABLE agent_connections",
    "DROP TABLE agents",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
