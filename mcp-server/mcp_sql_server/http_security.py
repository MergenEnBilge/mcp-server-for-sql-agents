"""What sits in front of the MCP endpoint on the HTTP transport, apart from authentication.

The MCP specification says a server "MUST validate the Origin header on all incoming
connections to prevent DNS rebinding attacks". A malicious web page can point its own hostname
at an address on the victim's network and then talk to whatever server answers there, using the
victim's browser. Checking that the Host and Origin headers name *this* server closes that door.

The SDK only switches that check on by itself when the server listens on a loopback address, so
a server bound to 0.0.0.0 in a container had it off. Here it is always on, and the allowed
values come from the server's own public address plus anything an operator adds.
"""

from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mcp_sql_server.config import Settings, split_list

# Most requests are a few hundred bytes; a 20,000-character query is about 120 KB once escaped.
MAX_REQUEST_BODY_BYTES = 256 * 1024

_DEFAULT_PORTS = {"http": 80, "https": 443}
_CLIENT_HEADERS = [
    "Authorization",
    "Content-Type",
    "Accept",
    "Mcp-Protocol-Version",
    "Mcp-Session-Id",
    "Last-Event-ID",
]


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """Host and Origin values this server answers to: its own public address, plus extras."""
    url = urlsplit(settings.public_url or "")
    hosts: list[str] = []
    origins: list[str] = []
    if url.hostname:
        host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        default_port = _DEFAULT_PORTS.get(url.scheme)
        hosts.append(url.netloc)
        if url.port in (None, default_port):
            # Clients may or may not spell out the default port.
            hosts += [host, f"{host}:{default_port}"]
        origins.append(f"{url.scheme}://{url.netloc}")
    hosts += split_list(settings.allowed_hosts)
    origins += split_list(settings.allowed_origins)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(set(hosts)),
        allowed_origins=sorted(set(origins)),
    )


def with_browser_access(app: ASGIApp, settings: Settings) -> ASGIApp:
    """Let web-page MCP clients from the configured origins call the endpoint. Nobody else gets
    cross-origin access: with no origins configured, browsers are simply not allowed in."""
    origins = split_list(settings.allowed_origins)
    if not origins:
        return app
    return CORSMiddleware(
        app,
        allow_origins=origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=_CLIENT_HEADERS,
        expose_headers=["WWW-Authenticate", "Mcp-Session-Id"],
        max_age=600,
    )


class SecurityHeaders:
    """Adds response headers that make no sense to change per route. Responses here are API
    answers for one signed-in caller: nothing should store them, or guess their type."""

    _HEADERS = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._HEADERS.items():
                    if name not in headers:  # a route that chose its own caching keeps it
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
