"""Who is calling? Turns an authenticated identity into the `Caller` the services use.

The services never look at tokens or environment variables; they only see a
`Caller`. That keeps authentication a concern of the transport layer: stdio
takes its identity from configuration, HTTP takes it from a validated OAuth token.
"""

from collections.abc import Callable
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token

from mcp_sql_server.config import Settings, split_list
from mcp_sql_server.errors import McpSqlError
from mcp_sql_server.models import Caller

CallerProvider = Callable[[], Caller]


class NotAuthenticated(McpSqlError):
    """A tool was called with no authenticated identity. Shouldn't happen behind the
    HTTP auth middleware; it's a backstop so an auth misconfiguration fails closed."""


def claim_at_path(claims: dict[str, Any], path: str) -> Any:
    """Follow a dotted path such as 'realm_access.roles' into nested claims."""
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def caller_from_claims(claims: dict[str, Any], roles_claim: str) -> Caller:
    roles = claim_at_path(claims, roles_claim)
    if isinstance(roles, str):  # some providers send a space- or comma-separated string
        roles = [r for r in roles.replace(",", " ").split() if r]
    return Caller(
        sub=str(claims["sub"]),
        name=claims.get("name") or claims.get("preferred_username") or claims.get("email"),
        roles=frozenset(str(r) for r in (roles or [])),
        client_id=client_id_from_claims(claims),
    )


def client_id_from_claims(claims: dict[str, Any]) -> str | None:
    """The OAuth client the token was issued to: `azp` in a JWT, `client_id` in an
    introspection answer. It is set by the identity provider, so an agent can't choose it."""
    value = claims.get("azp") or claims.get("client_id")
    return str(value) if value else None


def stdio_caller(settings: Settings) -> CallerProvider:
    """A fixed identity from configuration, for the local stdio transport."""
    if not settings.stdio_sub:
        raise ValueError(
            "The stdio transport has no login, so it needs an identity. Set MCP_STDIO_SUB "
            "(and optionally MCP_STDIO_NAME and MCP_STDIO_ROLES) to the user it should act as."
        )
    caller = Caller(
        sub=settings.stdio_sub,
        name=settings.stdio_name,
        roles=frozenset(split_list(settings.stdio_roles)),
    )
    return lambda: caller


def token_caller(roles_claim: str) -> CallerProvider:
    """The identity in the validated access token of the current HTTP request."""

    def current() -> Caller:
        token = get_access_token()
        if token is None or not token.claims:
            raise NotAuthenticated("Authentication is required.")
        caller = caller_from_claims(token.claims, roles_claim)
        if caller.client_id is None:
            # Without it there is no agent to approve, and "no agent" must never mean "no checks".
            raise NotAuthenticated(
                "The access token does not say which application it was issued to."
            )
        return caller

    return current
