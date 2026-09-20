"""The MCP server's whole service layer wired to the real databases, for integration tests."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mcp_sql_server.config import QueryLimits
from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.models import Caller
from mcp_sql_server.services.audit_service import AuditService
from mcp_sql_server.services.connection_registry import ConnectionRegistry
from mcp_sql_server.services.meta_store import PostgresMetaStore
from mcp_sql_server.services.permission_service import PermissionService
from mcp_sql_server.services.query_service import QueryService
from mcp_sql_server.services.query_validator import QueryValidator
from mcp_sql_server.services.sanitizer import OutputSanitizer
from mcp_sql_server.services.schema_service import SchemaService


@dataclass
class Stack:
    """The whole service layer wired to real databases, plus an admin handle for setup and checks."""

    schema: SchemaService
    query: QueryService
    permissions: PermissionService
    store: PostgresMetaStore
    registry: ConnectionRegistry
    admin: AsyncEngine  # connects as gui_app: can grant access and read the audit log
    caller: Caller  # a fresh analyst for this test, so audit rows are easy to pick out


@pytest.fixture
async def stack(postgres, registered: None, secret_box: SecretBox) -> AsyncIterator[Stack]:
    limits = QueryLimits(timeout_s=1.5)
    store = PostgresMetaStore.from_url(postgres.url("mcp_app", "app_meta"))
    admin = create_async_engine(postgres.url("gui_app", "app_meta"))
    registry = ConnectionRegistry(secret_box)
    permissions = PermissionService(store)
    audit = AuditService(store)
    sanitizer = OutputSanitizer(max_cell_chars=limits.max_cell_chars)
    try:
        yield Stack(
            schema=SchemaService(permissions, registry, audit, sanitizer, store),
            query=QueryService(
                permissions,
                registry,
                audit,
                sanitizer,
                QueryValidator(limits.max_sql_length),
                limits,
            ),
            permissions=permissions,
            store=store,
            registry=registry,
            admin=admin,
            caller=Caller(
                sub=f"it-{uuid4().hex[:10]}", name="Integration Test", roles=frozenset({"analyst"})
            ),
        )
    finally:
        await registry.close()
        await store.close()
        await admin.dispose()
