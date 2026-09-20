"""The record of what administrators did.

Every change made through the GUI writes a row to `admin_log` in the same transaction as the
change itself, so a change and its record either both exist or neither does. The log is
append-only (the database role can't edit or delete it), and `details` must never contain
secrets: callers describe *what* changed, not the values of credentials.
"""

from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection

from gui_backend.auth import User
from mcp_sql_server.cache.base import META, SCHEMA, Cache

_INSERT = text(
    "INSERT INTO admin_log (actor_sub, actor_name, action, target_type, target, details) "
    "VALUES (:sub, :name, :action, :target_type, :target, :details)"
).bindparams(bindparam("details", type_=JSONB))


async def log_change(
    db: AsyncConnection,
    user: User,
    action: str,
    target_type: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        _INSERT,
        {
            "sub": user.sub,
            "name": user.name,
            "action": action,
            "target_type": target_type,
            "target": target,
            "details": details or {},
        },
    )


async def announce(cache: Cache, *, meta: bool = True, schema: bool = False) -> None:
    """Tell the MCP server's caches that something changed, so they stop serving old answers.

    Called after the change has been committed. A no-op when no cache is configured.
    """
    if meta:
        await cache.bump(META)
    if schema:
        await cache.bump(SCHEMA)
