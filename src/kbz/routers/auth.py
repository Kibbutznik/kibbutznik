"""Magic-link auth endpoints for human users.

POST /auth/request-magic-link   — body: {email}. Returns {sent: true, link?: str}.
                                  `link` is only present when
                                  `auth_dev_expose_magic_link=True` (dev mode),
                                  so the viewer can show "click here" without
                                  a real SMTP integration.
GET  /auth/verify?token=...     — consumes magic-link, sets session cookie,
                                  returns JSON {user}. The viewer redirects
                                  back to the app on success.
POST /auth/logout               — revokes the current session + clears cookie.
GET  /auth/me                   — returns the currently-logged-in user or null.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import get_current_user
from kbz.config import settings
from kbz.database import get_db
from kbz.models.user import User
from kbz.services.auth_service import AuthService
from kbz.services.email_service import EmailService, render_magic_link_email

router = APIRouter(prefix="/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkResponse(BaseModel):
    sent: bool
    # Dev-mode convenience: the full verify URL. Hidden in prod.
    link: str | None = None


class MeResponse(BaseModel):
    user_id: str
    user_name: str
    email: str | None
    is_human: bool


def _set_session_cookie(resp: Response, raw_token: str) -> None:
    """Same flags on set + clear so browsers actually drop the cookie."""
    resp.set_cookie(
        key=settings.auth_session_cookie,
        value=raw_token,
        max_age=settings.auth_session_ttl_minutes * 60,
        httponly=True,
        samesite="lax",
        # secure=True should be set in prod via reverse proxy / TLS; we
        # leave it False here so local dev over http works.
        secure=False,
        path="/",
    )


def _clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(
        key=settings.auth_session_cookie,
        path="/",
        samesite="lax",
    )


@router.post("/request-magic-link", response_model=MagicLinkResponse)
async def request_magic_link(
    body: MagicLinkRequest,
    db: AsyncSession = Depends(get_db),
) -> MagicLinkResponse:
    """Create-or-fetch a human user for this email, mint a magic-link.

    Always sends the link via the configured EmailService (log backend in
    dev → captured in-memory; Resend backend → real email in prod).

    The `link` field in the response is exposed only in dev
    (`auth_dev_expose_magic_link=True`) as a convenience so the product
    UI can surface "click here" without needing a separate email client.
    In prod this field MUST be null.
    """
    svc = AuthService(db)
    user = await svc.get_or_create_human(body.email)
    issued = await svc.issue_magic_link(user)
    await db.commit()

    verify_path = f"/auth/verify?token={issued.raw}"
    # Email body NEEDS an absolute URL — email clients can't resolve a
    # relative href like "/auth/verify?..." and render it as
    # "http:///auth/verify?..." which fails. The in-app dev-link below
    # stays relative because the browser resolves it against the
    # current origin.
    base = (settings.public_base_url or "").rstrip("/")
    email_url = f"{base}{verify_path}" if base else verify_path
    msg = render_magic_link_email(verify_url=email_url)
    msg.to = user.email or body.email
    try:
        await EmailService().send(msg)
    except Exception:
        # Never fail this endpoint just because email couldn't go out.
        pass

    link: str | None = None
    if settings.auth_dev_expose_magic_link:
        link = verify_path
    return MagicLinkResponse(sent=True, link=link)


@router.get("/verify")
async def verify_magic_link(
    token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Consume a magic-link token, issue a session, set the cookie."""
    svc = AuthService(db)
    user = await svc.consume_magic_link(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired magic link",
        )
    session = await svc.issue_session(user)
    await db.commit()
    _set_session_cookie(response, session.raw)
    return {
        "user": {
            "user_id": str(user.id),
            "user_name": user.user_name,
            "email": user.email,
            "is_human": user.is_human,
        }
    }


@router.post("/logout")
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    kbz_session: str | None = Cookie(
        default=None, alias=settings.auth_session_cookie
    ),
) -> dict:
    if kbz_session:
        await AuthService(db).revoke_session(kbz_session)
        await db.commit()
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user: User | None = Depends(get_current_user)) -> dict:
    """Return the currently authenticated user, or null. Never errors."""
    if user is None:
        return {"user": None}
    return {
        "user": {
            "user_id": str(user.id),
            "user_name": user.user_name,
            "email": user.email,
            "is_human": user.is_human,
        }
    }
