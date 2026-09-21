"""The Host and Origin checks, and the extra response headers, on the HTTP transport."""

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from mcp_sql_server.config import Settings
from mcp_sql_server.http_security import SecurityHeaders, transport_security, with_browser_access


def settings(**overrides) -> Settings:
    return Settings(
        _env_file=None, app_meta_url="postgresql://x", connection_secret_keys="k", **overrides
    )


def test_the_servers_own_address_is_allowed_with_and_without_the_default_port():
    rules = transport_security(settings(public_url="https://mcp.example.com/mcp"))
    assert rules.enable_dns_rebinding_protection is True
    assert set(rules.allowed_hosts) == {"mcp.example.com", "mcp.example.com:443"}
    assert rules.allowed_origins == ["https://mcp.example.com"]


def test_a_custom_port_is_kept_exactly():
    rules = transport_security(settings(public_url="http://127.0.0.1:8000/mcp"))
    assert rules.allowed_hosts == ["127.0.0.1:8000"]
    assert rules.allowed_origins == ["http://127.0.0.1:8000"]


def test_operators_can_add_hosts_and_origins():
    rules = transport_security(
        settings(
            public_url="https://mcp.example.com/mcp",
            allowed_hosts="mcp-server:8000, internal.lan",
            allowed_origins="https://inspector.example.com",
        )
    )
    assert {"mcp-server:8000", "internal.lan", "mcp.example.com"} <= set(rules.allowed_hosts)
    assert "https://inspector.example.com" in rules.allowed_origins


def app_for(**overrides):
    async def home(_request):
        return PlainTextResponse("hi")

    return SecurityHeaders(
        with_browser_access(Starlette(routes=[Route("/", home)]), settings(**overrides))
    )


async def get(app, **headers):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/", headers=headers)


async def test_responses_are_not_cacheable_and_not_sniffed():
    response = await get(app_for())
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_a_web_page_gets_no_cross_origin_access_unless_its_origin_is_configured():
    nobody = await get(app_for(), Origin="https://evil.example")
    assert "access-control-allow-origin" not in nobody.headers

    app = app_for(allowed_origins="https://inspector.example.com")
    allowed = await get(app, Origin="https://inspector.example.com")
    assert allowed.headers["access-control-allow-origin"] == "https://inspector.example.com"
    other = await get(app, Origin="https://evil.example")
    assert "access-control-allow-origin" not in other.headers
