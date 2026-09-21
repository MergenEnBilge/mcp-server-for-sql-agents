"""Where agents connect to this server: the address the console shows on its Connect screen."""

from urllib.parse import urlsplit

from fastapi import APIRouter
from pydantic import BaseModel

from gui_backend.auth import Admin, ContextDep

router = APIRouter(prefix="/api", tags=["server"])


class ServerInfo(BaseModel):
    mcp_url: str | None  # None until an operator says what the public address is
    guide_url: str | None  # the guide agents can read, on the same host
    issuer: str  # the identity provider people sign in with
    secure: bool  # whether mcp_url is https (cloud chatbots need it)


@router.get("/server-info")
async def server_info(ctx: ContextDep, _admin: Admin) -> ServerInfo:
    url = ctx.settings.mcp_public_url
    origin = None
    if url:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
    return ServerInfo(
        mcp_url=url,
        guide_url=f"{origin}/agent-guide" if origin else None,
        issuer=ctx.settings.oauth_issuer,
        secure=bool(url and url.startswith("https://")),
    )
