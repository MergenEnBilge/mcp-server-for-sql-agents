"""Checks that a target database can be reached, and explains failures in plain words.

Drivers report failures in wildly different ways ("[Errno 111] Connect call failed",
"password authentication failed for user", ...). Administrators need to know what to fix,
so the common ones are translated into one sentence that says what is wrong.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from mcp_sql_server.adapters.sqlalchemy_adapter import SQLAlchemyAdapter
from mcp_sql_server.models import ConnectionRecord
from mcp_sql_server.services.connection_registry import build_engine_url

# Any of these words in a name marks a value as a secret that belongs in the encrypted
# `secret` field, never in the plain-text settings.
SECRET_LOOKING = re.compile(r"pass|secret|token|pwd|credential|apikey|api_key", re.IGNORECASE)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str
    table_count: int | None = None
    latency_ms: int | None = None


async def check_connection(
    engine: str,
    details: dict[str, Any],
    password: str | None,
    *,
    timeout_s: float,
    sqlite_root: str | None = None,
) -> CheckResult:
    """Open the database with the given settings, list its tables, and say how it went."""
    started = time.monotonic()
    record = ConnectionRecord(
        id=None,  # type: ignore[arg-type]  # not stored yet; only used to build the URL
        name="check",
        engine=engine,
        description="",
        details=details,
        secret_encrypted=None,
        updated_at=None,  # type: ignore[arg-type]
    )
    adapter = SQLAlchemyAdapter(
        build_engine_url(record, password, sqlite_root),
        schemas=details.get("schemas") or None,
        pool_size=1,
    )
    try:
        async with asyncio.timeout(timeout_s):
            await adapter.connect()
            tables = await adapter.list_tables()
    except TimeoutError:
        return CheckResult(
            False,
            f"Connection failed: no response from {_where(engine, details)} within {timeout_s:g}s.",
        )
    except Exception as exc:
        return CheckResult(False, "Connection failed: " + explain(exc, engine, details, password))
    finally:
        await adapter.close()
    elapsed = int((time.monotonic() - started) * 1000)
    return CheckResult(
        True,
        f"Connected. Found {len(tables)} table{'s' if len(tables) != 1 else ''}.",
        table_count=len(tables),
        latency_ms=elapsed,
    )


def _where(engine: str, details: dict[str, Any]) -> str:
    if engine == "sqlite":
        return f"file {details.get('path')}"
    port = details.get("port")
    return f"host {details.get('host')}" + (f" on port {port}" if port else "")


def explain(exc: BaseException, engine: str, details: dict[str, Any], password: str | None) -> str:
    """A one-sentence, password-free description of why a connection attempt failed."""
    text = _root_message(exc)
    lowered = text.lower()
    where = _where(engine, details)
    user = details.get("username")

    if "access denied" in lowered and "to database" in lowered:
        # MySQL says this both for a database that isn't there and for one the user can't use.
        message = (
            f"'{user}' can't use the database '{details.get('database')}': it either "
            "doesn't exist or that user has no rights on it."
        )
    elif (
        "password authentication failed" in lowered
        or "access denied" in lowered
        or "login failed" in lowered
    ):
        message = f"authentication failed for user '{user}'."
    elif ("does not exist" in lowered and "database" in lowered) or "unknown database" in lowered:
        message = f"the database '{details.get('database')}' does not exist on that server."
    elif "unable to open database file" in lowered or "no such file" in lowered:
        message = f"could not open {where}."
    elif any(
        s in lowered
        for s in (
            "connect call failed",
            "connection refused",
            "refused the network connection",  # Windows
            "errno 111",
            "10061",
            "winerror 1225",
        )
    ):
        message = f"could not reach {where}."
    elif any(
        s in lowered
        for s in ("name or service not known", "getaddrinfo", "nodename nor servname", "11001")
    ):
        message = f"could not resolve {where}."
    elif "timeout" in lowered or "timed out" in lowered:
        message = f"{where} did not respond in time."
    elif "no pg_hba.conf entry" in lowered or "ssl" in lowered:
        message = f"the server at {where} rejected the connection: {text}"
    else:
        message = text
    if password:
        message = message.replace(password, "***")
    return message[:300]


def _root_message(exc: BaseException) -> str:
    """The most informative message in an exception chain, first line only, class prefix removed."""
    current: BaseException = exc
    while current.__cause__ is not None or current.__context__ is not None:
        current = current.__cause__ or current.__context__  # type: ignore[assignment]
    message = (
        str(current).strip().splitlines()[0] if str(current).strip() else type(current).__name__
    )
    return re.sub(r"^<class '[^']+'>:\s*", "", message)
