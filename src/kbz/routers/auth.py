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

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import get_current_user
from kbz.config import settings
from kbz.database import get_db
from kbz.models.user import User
from kbz.services.auth_service import AuthService
from kbz.services.email_service import EmailService, render_magic_link_email
from kbz.services.rate_limit import magic_link_limiter

# Per-email: 5 magic-link requests per hour. Tuned to the actual human
# use case (request, mistype, re-request, wait for email, give up and
# retry) while still stopping bulk spam of someone else's inbox.
_EMAIL_LIMIT = 5
_EMAIL_WINDOW_S = 3600

# Per-IP: 30 per hour. Higher than per-email because a NATed office or
# mobile carrier legitimately produces many requests from one IP. Still
# low enough that a single box can't spray thousands of addresses.
_IP_LIMIT = 30
_IP_WINDOW_S = 3600

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
    # Backslashes normalize to '/' in Chrome/Safari Location headers, so
    # `/\evil.com/` ends up as `//evil.com/` in the address bar — an
    # open redirect through the back door. Strip any candidate with a
    # backslash. Similarly any whitespace (\t, \r, \n, space) before
    # the authority can confuse parsers; drop those too.
    if any(ch in candidate for ch in "\\ \t\r\n"):
        return _DEFAULT_POST_LOGIN
    # Disallow any scheme-like prefix (e.g. "/http://..." after decoding)
    if "://" in candidate:
        return _DEFAULT_POST_LOGIN
    return candidate


class MagicLinkRequest(BaseModel):
    email: EmailStr
    # "Remember me on this device" — long-lived session cookie. False
    # (default) gives a 1-day session suitable for shared machines.
    remember: bool = False


class MagicLinkResponse(BaseModel):
    sent: bool
    # Dev-mode convenience: the full verify URL. Hidden in prod.
    link: str | None = None


class MeResponse(BaseModel):
    user_id: str
    user_name: str
    email: str | None
    is_human: bool


def _set_session_cookie(
    resp: Response, raw_token: str, ttl_minutes: int | None = None,
) -> None:
    """Same flags on set + clear so browsers actually drop the cookie."""
    minutes = ttl_minutes if ttl_minutes is not None else settings.auth_session_ttl_minutes
    resp.set_cookie(
        key=settings.auth_session_cookie,
        value=raw_token,
        max_age=minutes * 60,
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


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For when present (we sit
    behind nginx in prod which sets it), else falls back to the raw peer.

    Only the LEFTMOST X-Forwarded-For entry is used — that's the client
    the edge proxy saw. Intermediate proxies append, so anything after
    the first comma is infrastructure, not the caller.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/request-magic-link", response_model=MagicLinkResponse)
async def request_magic_link(
    body: MagicLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MagicLinkResponse:
    """Create-or-fetch a human user for this email, mint a magic-link.

    Always sends the link via the configured EmailService (log backend in
    dev → captured in-memory; Resend backend → real email in prod).

    The `link` field in the response is exposed only in dev
    (`auth_dev_expose_magic_link=True`) as a convenience so the product
    UI can surface "click here" without needing a separate email client.
    In prod this field MUST be null.

    Rate-limited on two axes: per-email (to stop spraying one inbox) and
    per-IP (to stop a single attacker spraying many inboxes). Both must
    pass; whichever trips first wins the 429.
    """
    # Normalize email for rate-limit keying — otherwise "Alice@X.com" and
    # "alice@x.com" are separate buckets and the attacker trivially
    # doubles their budget.
    email_key = body.email.strip().lower()
    ip = _client_ip(request)

    email_hit = magic_link_limiter.check(
        key=f"email:{email_key}", limit=_EMAIL_LIMIT, window_s=_EMAIL_WINDOW_S,
    )
    ip_hit = magic_link_limiter.check(
        key=f"ip:{ip}", limit=_IP_LIMIT, window_s=_IP_WINDOW_S,
    )
    if not email_hit.allowed or not ip_hit.allowed:
        retry = max(email_hit.retry_after_s, ip_hit.retry_after_s)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many magic-link requests. Try again shortly.",
            headers={"Retry-After": str(retry)},
        )

    svc = AuthService(db)
    user = await svc.get_or_create_human(body.email)
    issued = await svc.issue_magic_link(user)
    await db.commit()

    remember_qs = "&remember=1" if body.remember else ""
    verify_path = f"/auth/verify?token={issued.raw}{remember_qs}"
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
    remember: int = 0,
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
    ttl = (
        settings.auth_session_ttl_minutes if remember
        else settings.auth_session_short_ttl_minutes
    )
    session = await svc.issue_session(user, ttl_minutes=ttl)
    await db.commit()

    if next is not None:
        redirect = RedirectResponse(url=_safe_next_path(next), status_code=303)
        _set_session_cookie(redirect, session.raw, ttl_minutes=ttl)
        return redirect

    _set_session_cookie(response, session.raw, ttl_minutes=ttl)
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
async def me(
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the currently authenticated user, or null. Never errors.

    Side effect: on the FIRST /auth/me call for a fresh human user
    (no `user` wallet yet and `welcome_credits` > 0 in config), mints
    a welcome-credits gift into a new user wallet. Makes the escrow-
    based membership-fee flow tractable for brand-new accounts.
    """
    if user is None:
        return {"user": None}
    if user.is_human:
        try:
            await _provision_welcome_credits(db, user)
            await db.commit()
        except Exception:
            # Never fail /auth/me on a provisioning hiccup — the user
            # just doesn't get credits this round; next /auth/me
            # retries.
            await db.rollback()
    return {
        "user": {
            "user_id": str(user.id),
            "user_name": user.user_name,
            "email": user.email,
            "is_human": user.is_human,
        }
    }


async def _provision_welcome_credits(db: AsyncSession, user: User) -> None:
    """Mint `welcome_credits` into the user's wallet IF (a) they don't
    have one yet and (b) the amount is > 0.

    Idempotent by the "don't have a wallet yet" check — once the
    wallet exists we never top it up here again. Explicit bonuses
    beyond the initial gift should go through admin-only tooling.
    """
    from decimal import Decimal
    from kbz.services.wallet_service import WalletService, OWNER_USER

    try:
        amount = Decimal(settings.welcome_credits)
    except Exception:
        return
    if amount <= 0:
        return

    svc = WalletService(db)
    # Race-safe idempotency: gate on the wallet_webhook_events
    # dedupe table (UNIQUE on (event, idempotency_key)). Two
    # concurrent /auth/me calls used to both pass the wallet-existence
    # check, both call get_or_create, and both mint — doubling the
    # gift. The unique index now turns the loser's record_webhook
    # INSERT into a clean IntegrityError; we rollback that mint and
    # bail.
    from sqlalchemy.exc import IntegrityError
    welcome_event = "welcome.signup"
    welcome_key = str(user.id)
    # Fast-path check (no race protection — purely to skip the work
    # when the gift was already minted in a past request).
    existing = await svc.find_webhook(
        event=welcome_event, idempotency_key=welcome_key,
    )
    if existing is not None:
        return

    wallet = await svc.get_or_create(OWNER_USER, user.id, gate=False)
    entry = await svc.mint(
        wallet, amount,
        webhook_event=welcome_event,
        external_ref=f"welcome:{user.id}",
        memo="Welcome to Kibbutznik — starter credits",
    )
    try:
        await svc.record_webhook(
            event=welcome_event,
            idempotency_key=welcome_key,
            ledger_entry_id=entry.id,
        )
    except IntegrityError:
        # Lost the race — another /auth/me already provisioned this
        # user. Roll back our duplicate mint so the user doesn't
        # walk away with double credits.
        await db.rollback()
        return
