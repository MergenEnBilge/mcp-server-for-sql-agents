"""Fixtures shared by every integration test: real databases, real migrations, real encryption.

A throwaway Postgres 16 container is started with the same init script and sample
data as the dev setup (db/init, db/sample_data), then the real Alembic migration is
applied to app_meta. The SQLite copy of the sample data is built from the same SQL.
Needs Docker; the tests skip themselves if it isn't running.
"""

import asyncio
import importlib.util
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.devtools.seed import PostgresTarget, seed_sample_registry

sys.path.insert(0, str(Path(__file__).parent / "support"))
REPO = Path(__file__).resolve().parents[1]
ROLES = ("META_OWNER", "MCP_APP", "GUI_APP", "ORG_OWNER", "ORG_READONLY")


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)


@dataclass(frozen=True)
class PostgresInfra:
    host: str
    port: int
    passwords: dict[str, str]

    def url(self, role: str, database: str) -> str:
        return (
            f"postgresql+asyncpg://{role}:{self.passwords[role.upper()]}"
            f"@{self.host}:{self.port}/{database}"
        )


async def _wait_until_ready(url: str, timeout_s: float = 90) -> None:
    engine = create_async_engine(url)
    deadline = asyncio.get_running_loop().time() + timeout_s
    try:
        while True:
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                return
            except Exception:
                if asyncio.get_running_loop().time() > deadline:
                    raise
                await asyncio.sleep(1)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresInfra]:
    try:
        import docker
        from testcontainers.core.container import DockerContainer

        docker.from_env().ping()
    except Exception as exc:
        pytest.skip(f"Docker is not available: {exc}")

    passwords = {role: secrets.token_hex(8) for role in ROLES}
    container = (
        DockerContainer("postgres:16-alpine")
        .with_env("POSTGRES_USER", "postgres")
        .with_env("POSTGRES_PASSWORD", secrets.token_hex(8))
        .with_exposed_ports(5432)
        .with_volume_mapping(str(REPO / "db" / "init"), "/docker-entrypoint-initdb.d", "ro")
        .with_volume_mapping(str(REPO / "db" / "sample_data"), "/sample_data", "ro")
    )
    for role, password in passwords.items():
        container = container.with_env(f"{role}_PASSWORD", password)

    with container:
        infra = PostgresInfra(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            passwords=passwords,
        )
        asyncio.run(_wait_until_ready(infra.url("meta_owner", "app_meta")))

        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO / "db",
            env={**os.environ, "APP_META_MIGRATIONS_URL": infra.url("meta_owner", "app_meta")},
            capture_output=True,
            text=True,
        )
        assert migrated.returncode == 0, f"migration failed:\n{migrated.stderr}"
        yield infra


@pytest.fixture(scope="session")
def sqlite_file(tmp_path_factory) -> Iterator[Path]:
    spec = importlib.util.spec_from_file_location(
        "build_sqlite", REPO / "db" / "sample_data" / "build_sqlite.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path_factory.mktemp("sqlite") / "sample.sqlite"
    module.build(path)
    # SQLite connections are only allowed inside one configured folder; this is that folder.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MCP_SQLITE_ROOT", str(path.parent))
        patch.setenv("GUI_SQLITE_ROOT", str(path.parent))
        yield path


@pytest.fixture(scope="session")
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture(scope="session")
def secret_box(fernet_key: str) -> SecretBox:
    return SecretBox(fernet_key)


@pytest.fixture(scope="session")
def registered(postgres: PostgresInfra, sqlite_file: Path, secret_box: SecretBox) -> None:
    """Register both sample databases, as the GUI's database role would."""

    async def seed() -> None:
        engine = create_async_engine(postgres.url("gui_app", "app_meta"))
        try:
            await seed_sample_registry(
                engine,
                secret_box,
                postgres=PostgresTarget(
                    host=postgres.host,
                    port=postgres.port,
                    database="org_data",
                    username="org_readonly",
                    password=postgres.passwords["ORG_READONLY"],
                ),
                sqlite_path=str(sqlite_file),
                approved_agents=("test-client",),  # what fake_idp.py signs its tokens for
            )
        finally:
            await engine.dispose()

    asyncio.run(seed())
