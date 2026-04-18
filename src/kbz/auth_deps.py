"""FastAPI dependencies for human-user session auth.

Two flavors:

- `get_current_user` — reads the session cookie, returns a User or None.
  Never raises. Use on any route that wants to know "is this request from
  a logged-in human" but still works for anonymous or agent requests.

- `require_user` — same lookup, but raises 401 if no valid session. Use
  on routes that only humans should hit (invite creation, profile).

Agents (server-internal) never set the session cookie, so they simply
return None from `get_current_user`. Existing endpoints that accept
`user_id` in the body remain usable by agents.
"""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.config import settings
from kbz.database import get_db
from kbz.models.user import User
from kbz.services.auth_service import AuthService


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    kbz_session: str | None = Cookie(
        default=None, alias=settings.auth_session_cookie
    ),
) -> User | None:
    if not kbz_session:
        return None
    return await AuthService(db).resolve_session(kbz_session)


async def require_user(
    user: User | None = Depends(get_current_user),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return user
