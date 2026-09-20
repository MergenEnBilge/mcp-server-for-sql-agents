"""Who is asking, and are they an administrator?

The GUI sits behind the same identity provider as the MCP server. The browser app signs the
person in and sends the resulting access token as a Bearer token; this API only checks it
(signature, expiry, issuer, and that it was issued *for this API*). Nothing about a session
lives on the server.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gui_backend.context import Context
from mcp_sql_server.auth.caller import caller_from_claims

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class User:
    sub: str
    name: str | None
    roles: frozenset[str]
    is_admin: bool


def get_context(request: Request) -> Context:
    ctx: Context = request.app.state.ctx
    return ctx


ContextDep = Annotated[Context, Depends(get_context)]


def _unauthorized(error: str, description: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail=description,
        headers={"WWW-Authenticate": f'Bearer error="{error}", error_description="{description}"'},
    )


async def current_user(
    ctx: ContextDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access = await ctx.verifier.verify_token(credentials.credentials)
    if access is None or not access.claims:
        raise _unauthorized("invalid_token", "The access token is invalid or has expired.")

    caller = caller_from_claims(access.claims, ctx.settings.oauth_roles_claim)
    admin = ctx.settings.admin_role
    return User(
        sub=caller.sub,
        name=caller.name,
        roles=caller.roles,
        is_admin=admin in caller.roles or admin in access.scopes,
    )


async def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This action needs an administrator.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
Admin = Annotated[User, Depends(require_admin)]
