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
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import get_current_user
from kbz.config import settings
from kbz.database import get_db
from kbz.models.user import User
from kbz.services.auth_service import AuthService
from kbz.services.email_service import EmailService, render_magic_link_email

router = APIRouter(prefix="/auth", tags=["auth"])


_DEFAULT_POST_LOGIN = "/app/#/dashboard"


def _safe_next_path(candidate: str | None) -> str:
    """Anti-open-redirect: accept only relative paths starting with a
    single '/'. Reject protocol-relative ('//evil.com/...') and anything
    with a scheme. Falls back to the default post-login destination.
    """
    if not candidate:
        return _DEFAULT_POST_LOGIN
    if not candidate.startswith("/"):
        return _DEFAULT_POST_LOGIN
    if candidate.startswith("//"):
        return _DEFAULT_POST_LOGIN
    # Disallow any scheme-like prefix (e.g. "/http://..." after decoding)
    if "://" in candidate:
        return _DEFAULT_POST_LOGIN
    return candidate


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
    # Email body NEEDS (a) an absolute URL — email clients can't resolve
    # a relative href and render it as "http:///auth/verify?..." which
    # fails; and (b) a `next=` param — so clicking the link lands on
    # the app dashboard instead of a raw JSON response.
    # The in-app dev-link below stays bare: the viewer JS consumes it
    # via fetch and navigates to the dashboard itself.
    base = (settings.public_base_url or "").rstrip("/")
    from urllib.parse import quote
    email_verify_path = f"{verify_path}&next={quote('/app/#/dashboard', safe='/#')}"
    email_url = f"{base}{email_verify_path}" if base else email_verify_path
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
    next: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Consume a magic-link token, issue a session, set the cookie.

    Two response shapes:

    - **No `next` param** (default): returns JSON `{"user": {...}}`. Used
      by the viewer's dev-mode one-click flow that calls this via fetch
      and then navigates itself.
    - **With `next=/some/path`**: returns a 303 See Other redirect to
      that path. Used by email links so clicking in an inbox lands on
      the app dashboard instead of showing raw JSON.

    The `next` path is validated to be a safe relative URL — no
    protocol-relative, no scheme. Anything else falls back to the
    default dashboard path.
    """
    svc = AuthService(db)
    user = await svc.consume_magic_link(token)
    if user is None:
        if next is not None:
            # Browser click on an expired/bad link — send them to the
            # login page with a flag rather than a raw 400 error page.
            return RedirectResponse(
                url="/app/#/login?error=expired", status_code=303,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired magic link",
        )
    session = await svc.issue_session(user)
    await db.commit()

    if next is not None:
        redirect = RedirectResponse(url=_safe_next_path(next), status_code=303)
        _set_session_cookie(redirect, session.raw)
        return redirect

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
