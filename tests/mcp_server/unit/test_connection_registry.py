"""Building live adapters from stored connection records."""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import FakeAdapter
from cryptography.fernet import Fernet

from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.errors import ConnectionUnavailable
from mcp_sql_server.models import ConnectionRecord
from mcp_sql_server.services.connection_registry import (
    ConnectionRegistry,
    build_engine_url,
    driver_problem,
)

KEY = Fernet.generate_key().decode()
SECRET_BOX = SecretBox(KEY)
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def record(
    engine="postgresql", details=None, password="s3cret!", updated_at=NOW
) -> ConnectionRecord:
    return ConnectionRecord(
        id=uuid4(),
        name="shop",
        engine=engine,
        description="",
        details=details or {},
        secret_encrypted=SECRET_BOX.encrypt(password) if password else None,
        updated_at=updated_at,
    )


# --- URLs come from configuration only ---------------------------------------------------------


def test_postgres_url_is_built_from_the_record():
    url = build_engine_url(
        record(
            details={"host": "db.internal", "port": 5432, "database": "shop", "username": "reader"}
        ),
        "s3cret!",
    )
    assert (
        url.render_as_string(hide_password=False)
        == "postgresql+asyncpg://reader:s3cret%21@db.internal:5432/shop"
    )


def test_each_supported_engine_picks_its_async_driver():
    details = {"host": "h", "database": "d", "username": "u"}
    assert build_engine_url(record("mysql", details), "p").drivername == "mysql+aiomysql"
    assert build_engine_url(record("mssql", details), "p").drivername == "mssql+aioodbc"
    assert build_engine_url(record("postgresql", details), "p").drivername == "postgresql+asyncpg"


def test_sqlite_uses_a_file_path_and_no_credentials(tmp_path):
    url = build_engine_url(
        record("sqlite", {"path": str(tmp_path / "shop.sqlite")}, password=None),
        None,
        str(tmp_path),
    )
    assert (url.drivername, url.database, url.username) == (
        "sqlite+aiosqlite",
        str((tmp_path / "shop.sqlite").resolve()),
        None,
    )


def test_driver_options_are_passed_through():
    url = build_engine_url(record(details={"host": "h", "options": {"sslmode": "require"}}), "p")
    assert dict(url.query) == {"sslmode": "require"}


def test_sql_server_gets_a_default_odbc_driver_unless_one_is_given():
    details = {"host": "h", "database": "d", "username": "u"}
    assert build_engine_url(record("mssql", details), "p").query == {
        "driver": "ODBC Driver 18 for SQL Server"
    }
    chosen = {**details, "options": {"driver": "ODBC Driver 17 for SQL Server", "Encrypt": "yes"}}
    assert dict(build_engine_url(record("mssql", chosen), "p").query) == {
        "driver": "ODBC Driver 17 for SQL Server",
        "Encrypt": "yes",
    }


def test_an_engine_whose_driver_is_missing_says_so(monkeypatch):
    assert driver_problem("sqlite") is None
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert "'aiomysql' driver isn't installed" in (driver_problem("mysql") or "")
    assert driver_problem("oracle") == "This engine isn't supported."


def test_an_unknown_engine_is_an_error():
    with pytest.raises(ValueError, match="unsupported engine"):
        build_engine_url(record("oracle"), "p")


# --- adapter lifecycle -------------------------------------------------------------------------


class Recorder:
    """Stands in for the SQLAlchemyAdapter class so no real connection is attempted."""

    def __init__(self) -> None:
        self.created: list[tuple[str, list[str] | None]] = []
        self.adapters: list[FakeAdapter] = []
        self.closed: list[FakeAdapter] = []
        self.fail_with: Exception | None = None

    def __call__(self, url, schemas=None) -> FakeAdapter:
        self.created.append((url.render_as_string(hide_password=False), schemas))
        adapter = FakeAdapter()
        if self.fail_with:

            async def boom() -> None:
                raise self.fail_with  # type: ignore[misc]

            adapter.connect = boom  # type: ignore[method-assign]

        async def close() -> None:
            self.closed.append(adapter)

        adapter.close = close  # type: ignore[method-assign]
        self.adapters.append(adapter)
        return adapter


async def test_the_password_is_decrypted_only_to_open_the_connection():
    factory = Recorder()
    registry = ConnectionRegistry(SECRET_BOX, factory)
    await registry.adapter_for(record(details={"host": "h", "database": "d", "username": "u"}))
    assert ":s3cret%21@" in factory.created[0][0]


async def test_extra_schemas_from_the_record_reach_the_adapter():
    factory = Recorder()
    registry = ConnectionRegistry(SECRET_BOX, factory)
    await registry.adapter_for(record(details={"host": "h", "schemas": ["sales"]}))
    assert factory.created[0][1] == ["sales"]


async def test_the_same_record_reuses_one_adapter():
    factory = Recorder()
    registry = ConnectionRegistry(SECRET_BOX, factory)
    rec = record(details={"host": "h"})
    assert await registry.adapter_for(rec) is await registry.adapter_for(rec)
    assert len(factory.created) == 1


async def test_editing_a_connection_replaces_the_adapter_and_closes_the_old_one():
    factory = Recorder()
    registry = ConnectionRegistry(SECRET_BOX, factory)
    original = record(details={"host": "old-host"})
    first = await registry.adapter_for(original)

    edited = replace(
        original,
        details={"host": "new-host"},
        updated_at=original.updated_at + timedelta(minutes=5),
    )
    second = await registry.adapter_for(edited)

    assert second is not first
    assert factory.closed == [first]
    assert "new-host" in factory.created[1][0]


async def test_failure_to_connect_is_reported_generically_and_logged_without_the_password(caplog):
    factory = Recorder()
    factory.fail_with = OSError("connection refused")
    registry = ConnectionRegistry(SECRET_BOX, factory)

    with caplog.at_level(logging.ERROR), pytest.raises(ConnectionUnavailable) as caught:
        await registry.adapter_for(record(details={"host": "h"}))

    assert "connection refused" not in str(caught.value)  # operators see that, the LLM doesn't
    assert "shop" in str(caught.value)
    assert "connection refused" in caplog.text
    assert "s3cret" not in caplog.text
    assert "s3cret" not in str(caught.value)


async def test_a_secret_that_cannot_be_decrypted_is_a_connection_problem_not_a_crash():
    other_key = SecretBox(Fernet.generate_key().decode())
    registry = ConnectionRegistry(other_key, Recorder())
    with pytest.raises(ConnectionUnavailable):
        await registry.adapter_for(record(details={"host": "h"}))


async def test_close_closes_every_adapter():
    factory = Recorder()
    registry = ConnectionRegistry(SECRET_BOX, factory)
    await registry.adapter_for(record(details={"host": "a"}))
    await registry.adapter_for(record(details={"host": "b"}))
    await registry.close()
    assert len(factory.closed) == 2
