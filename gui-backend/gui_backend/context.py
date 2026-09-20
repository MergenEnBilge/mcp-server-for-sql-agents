"""Everything a request handler needs, created once at startup."""

from dataclasses import dataclass

from mcp.server.auth.provider import TokenVerifier
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from gui_backend.config import Settings
from mcp_sql_server.auth.token_verifier import JwtTokenVerifier
from mcp_sql_server.cache.base import Cache
from mcp_sql_server.cache.redis_cache import RedisCache
from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.services.connection_registry import ConnectionRegistry


@dataclass
class Context:
    settings: Settings
    engine: AsyncEngine
    cache: Cache
    secret_box: SecretBox
    registry: ConnectionRegistry  # opens the *target* databases, for browsing their tables
    verifier: TokenVerifier

    async def close(self) -> None:
        await self.registry.close()
        await self.cache.close()
        await self.engine.dispose()


def build_context(
    settings: Settings, *, cache: Cache | None = None, verifier: TokenVerifier | None = None
) -> Context:
    """Create the shared resources. `cache` and `verifier` can be supplied by tests."""
    from mcp_sql_server.cache.base import NullCache

    if cache is None:
        cache = (
            RedisCache.from_url(settings.redis_url.get_secret_value())
            if settings.redis_url
            else NullCache()
        )
    secret_box = SecretBox(settings.connection_secret_keys.get_secret_value())
    return Context(
        settings=settings,
        engine=create_async_engine(
            settings.app_meta_url.get_secret_value(), pool_size=10, pool_pre_ping=True
        ),
        cache=cache,
        secret_box=secret_box,
        registry=ConnectionRegistry(
            secret_box, cache=cache, schema_ttl_s=settings.schema_cache_ttl_s
        ),
        verifier=verifier
        or JwtTokenVerifier(
            issuer=settings.oauth_issuer,
            audience=settings.oauth_audience,
            jwks_url=settings.oauth_jwks_url,
        ),
    )
