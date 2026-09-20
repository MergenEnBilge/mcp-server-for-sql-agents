"""The signed-in person, as the API sees them."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from gui_backend.auth import ContextDep, CurrentUser

router = APIRouter(prefix="/api", tags=["session"])


class Me(BaseModel):
    sub: str
    name: str | None
    roles: list[str]
    is_admin: bool


@router.get("/me")
async def me(user: CurrentUser, ctx: ContextDep) -> Me:
    """Who am I, and may I administer? The UI calls this on load. It also remembers the person
    so they can be offered in permission grids."""
    async with ctx.engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO known_subjects (subject_type, subject_id, display_name) "
                "VALUES ('user', :sub, :name) ON CONFLICT (subject_type, subject_id) DO UPDATE "
                "SET last_seen_at = now(), "
                "display_name = COALESCE(EXCLUDED.display_name, known_subjects.display_name)"
            ),
            {"sub": user.sub, "name": user.name},
        )
    return Me(sub=user.sub, name=user.name, roles=sorted(user.roles), is_admin=user.is_admin)
