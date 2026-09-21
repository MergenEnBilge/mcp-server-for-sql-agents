"""The admin API, exercised through HTTP against real Postgres. Tokens come from a fake identity
provider, so these tests prove what the API does with them."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fake_cache import MemoryCache
from fake_idp import FakeIdP
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from gui_backend.config import Settings
from gui_backend.context import Context
from gui_backend.main import create_app
from mcp_sql_server.auth.token_verifier import JwtTokenVerifier

GUI_ISSUER = "https://idp.test/realms/shop"  # same identity provider as the MCP server
GUI_AUDIENCE = "https://gui.test/api"


@dataclass
class Api:
    client: httpx.AsyncClient
    idp: FakeIdP
    cache: MemoryCache
    owner: AsyncEngine  # meta_owner: can seed rows the GUI's own role isn't allowed to write
    ctx: Context  # the app's shared resources, for tests that need to swap one

    def headers(
        self, *roles: str, sub: str = "admin-1", name: str = "Ada Admin", **kw: Any
    ) -> dict:
        token = self.idp.token(sub=sub, name=name, roles=roles, aud=GUI_AUDIENCE, **kw)
        return {"Authorization": f"Bearer {token}"}

    @property
    def admin(self) -> dict:
        return self.headers("admin")

    @property
    def member(self) -> dict:
        """Signed in, but not an administrator."""
        return self.headers("analyst", sub="member-1", name="Mia Member")


@pytest.fixture
async def api(postgres, fernet_key, registered) -> AsyncIterator[Api]:
    idp = FakeIdP()
    cache = MemoryCache()
    settings = Settings(
        _env_file=None,
        app_meta_url=postgres.url("gui_app", "app_meta"),
        connection_secret_keys=fernet_key,
        oauth_issuer=GUI_ISSUER,
        oauth_audience=GUI_AUDIENCE,
        connection_test_timeout_s=5,
    )
    verifier = JwtTokenVerifier(
        issuer=GUI_ISSUER, audience=GUI_AUDIENCE, http_client=idp.http_client()
    )
    app = create_app(settings, cache=cache, verifier=verifier)
    owner = create_async_engine(postgres.url("meta_owner", "app_meta"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gui.test"
    ) as client:
        yield Api(client, idp, cache, owner, app.state.ctx)
    await app.state.ctx.close()
    await owner.dispose()
