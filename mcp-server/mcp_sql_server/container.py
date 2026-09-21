"""Builds the service layer from settings. The one place everything is wired together."""

from dataclasses import dataclass, field

from mcp_sql_server.cache.base import Cache
from mcp_sql_server.cache.cached_meta_store import CachedMetaStore
from mcp_sql_server.cache.redis_cache import create_cache
from mcp_sql_server.config import Settings
from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.ratelimit import RateLimiter
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
    cache: Cache
    registry: ConnectionRegistry
    permissions: PermissionService
    schema: SchemaService
    query: QueryService
    limiter: RateLimiter = field(default_factory=RateLimiter)

    async def close(self) -> None:
        await self.registry.close()
        await self.store.close()


def build_services(
    settings: Settings, store: MetaStore | None = None, cache: Cache | None = None
) -> Services:
    limits = settings.query_limits()
    cache = cache or create_cache(settings)
    store = CachedMetaStore(
        store or PostgresMetaStore.from_url(settings.app_meta_url.get_secret_value()),
        cache,
        permission_ttl_s=settings.permission_cache_ttl_s,
        description_ttl_s=settings.cache_ttl_s,
    )
    registry = ConnectionRegistry(
        SecretBox(settings.connection_secret_keys.get_secret_value()),
        cache=cache,
        schema_ttl_s=settings.cache_ttl_s,
        sqlite_root=settings.sqlite_root,
    )
    permissions = PermissionService(store)
    audit = AuditService(store)
    sanitizer = OutputSanitizer(max_cell_chars=limits.max_cell_chars)
    return Services(
        limiter=RateLimiter(
            cache,
            per_minute=settings.rate_limit_per_minute,
            max_concurrent=settings.max_concurrent_calls,
        ),
        store=store,
        cache=cache,
        registry=registry,
        permissions=permissions,
        schema=SchemaService(permissions, registry, audit, sanitizer, store),
        query=QueryService(
            permissions, registry, audit, sanitizer, QueryValidator(limits.max_sql_length), limits
        ),
    )
