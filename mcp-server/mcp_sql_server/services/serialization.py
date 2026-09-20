"""Turns database values into plain JSON-friendly ones, the same way on every engine."""

import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID


def to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        # A JSON number where that loses nothing (so Postgres NUMERIC and SQLite REAL look
        # alike to the caller), an exact string where it would.
        as_float = float(value)
        return (
            as_float if math.isfinite(as_float) and Decimal(repr(as_float)) == value else str(value)
        )
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<binary, {len(bytes(value))} bytes>"
    if isinstance(value, list | tuple | set | frozenset):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    return str(value)
