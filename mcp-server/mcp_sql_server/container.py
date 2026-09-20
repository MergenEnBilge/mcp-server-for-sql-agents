"""Builds the service layer from settings. The one place everything is wired together."""

from dataclasses import dataclass

from mcp_sql_server.config import Settings
from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.services.audit_service import AuditService
from mcp_sql_server.services.connection_registry import ConnectionRegistry
from mcp_sql_server.services.meta_store import MetaStore, PostgresMetaStore
from mcp_sql_server.services.permission_service import PermissionService
from mcp_sql_server.services.query_service import QueryService
from mcp_sql_server.services.query_validator import QueryValidator
from mcp_sql_server.services.sanitizer import OutputSanitizer
from mcp_sql_server.services.schema_service import SchemaService


@dataclass
class Services:
    """Everything the MCP tools need. Holds the connections that must be closed on shutdown."""

    store: MetaStore
    registry: ConnectionRegistry
    permissions: PermissionService
    schema: SchemaService
    query: QueryService

    async def close(self) -> None:
        await self.registry.close()
        await self.store.close()


def build_services(settings: Settings, store: MetaStore | None = None) -> Services:
    limits = settings.query_limits()
    store = store or PostgresMetaStore.from_url(settings.app_meta_url.get_secret_value())
    registry = ConnectionRegistry(SecretBox(settings.connection_secret_keys.get_secret_value()))
    permissions = PermissionService(store)
    audit = AuditService(store)
    sanitizer = OutputSanitizer(max_cell_chars=limits.max_cell_chars)
    return Services(
        store=store,
        registry=registry,
        permissions=permissions,
        schema=SchemaService(permissions, registry, audit, sanitizer, store),
        query=QueryService(
            permissions, registry, audit, sanitizer, QueryValidator(limits.max_sql_length), limits
        ),
    )
