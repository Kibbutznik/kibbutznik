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


def enforce_session_matches_body(
    body_user_id,
    session_user: User | None,
) -> None:
    """If a session cookie is present, the body's user_id MUST match it.

    Use this at the top of any write endpoint that accepts a `user_id` in
    its request body (support, comment, support_pulse, create_proposal,
    …). Agents — which never carry a session cookie — are unaffected
    because `session_user` will be None.

    Raises 403 if a logged-in human tries to spoof another user.
    """
    if session_user is None:
        return
    # body_user_id may be uuid.UUID or str depending on pydantic model
    try:
        same = str(body_user_id) == str(session_user.id)
    except Exception:
        same = False
    if not same:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session user does not match body.user_id",
        )
