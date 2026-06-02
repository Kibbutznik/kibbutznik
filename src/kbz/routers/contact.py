"""Public "Get in touch" contact form.

POST /contact — anyone can leave a message. Persist FIRST (so nothing is
lost to an email outage), then best-effort email the operator. Anti-abuse
mirrors the rest of the hardened surface: per-IP rate limit + honeypot +
length caps (no CAPTCHA — we never add or bypass those).

GET /admin/contact — admin-gated (settings.admin_user_ids) inbox so the
operator can read submissions even if every notification email bounced.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import get_current_user
from kbz.config import settings
from kbz.database import get_db
from kbz.models.contact_message import ContactMessage
from kbz.models.user import User
from kbz.request_ip import client_ip
from kbz.schemas.contact import (
    ContactCreate,
    ContactMessageOut,
    ContactResponse,
    SendMailIn,
    SendMailResponse,
)
from kbz.services.email_service import EmailMessage, EmailService
from kbz.services.rate_limit import magic_link_limiter

logger = logging.getLogger("kbz.contact")

router = APIRouter()

# Per-IP submission cap. Generous enough for a real person who sends a
# follow-up, tight enough that the form isn't a spam relay.
_RATE_LIMIT = 5
_RATE_WINDOW_S = 3600


@router.post("/contact", response_model=ContactResponse)
async def submit_contact(
    data: ContactCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Honeypot: a hidden field real users never see. If it's filled, a bot
    # did it — return a cheerful 200 and drop the message so the bot
    # doesn't learn it was caught.
    if data.website:
        return ContactResponse(ok=True)

    ip = client_ip(request)
    hit = magic_link_limiter.check(
        key=f"contact:{ip}", limit=_RATE_LIMIT, window_s=_RATE_WINDOW_S
    )
    if not hit.allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many messages — please try again later."},
            headers={"Retry-After": str(hit.retry_after_s)},
        )

    row = ContactMessage(
        name=(data.name or None),
        email=(str(data.email) if data.email else None),
        message=data.message,
        ip=ip,
        user_agent=(request.headers.get("user-agent") or None),
    )
    db.add(row)
    await db.commit()

    # Best-effort notification. NEVER fail the submit on an email error —
    # the message is already safely persisted (mirrors the magic-link
    # degrade pattern).
    notify = settings.contact_notify_email
    if notify:
        try:
            who = data.name or data.email or "anonymous"
            preview = data.message.strip().splitlines()[0][:80] if data.message.strip() else ""
            await EmailService().send(EmailMessage(
                to=notify,
                subject=f"Kibbutznik contact from {who}: {preview}",
                text=(
                    f"From: {data.name or '(no name)'} <{data.email or 'no email'}>\n"
                    f"IP: {ip}\n\n{data.message}"
                ),
                reply_to=(str(data.email) if data.email else None),
            ))
        except Exception:
            logger.warning("contact notify email failed (message %s persisted)", row.id)

    return ContactResponse(ok=True)


@router.get("/admin/contact", response_model=list[ContactMessageOut])
async def list_contact_messages(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Admin-gated inbox. Same gate as /admin/errors: locked entirely
    unless the caller is in settings.admin_user_ids."""
    admin_ids = {
        x.strip() for x in (settings.admin_user_ids or "").split(",") if x.strip()
    }
    if not admin_ids:
        return JSONResponse(status_code=403, content={"detail": "admin not configured"})
    if user is None or str(user.id) not in admin_ids:
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    limit = max(1, min(limit, 500))
    rows = (
        await db.execute(
            select(ContactMessage)
            .order_by(ContactMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return rows


def _is_admin(user: User | None) -> bool:
    admin_ids = {
        x.strip() for x in (settings.admin_user_ids or "").split(",") if x.strip()
    }
    return bool(admin_ids) and user is not None and str(user.id) in admin_ids


@router.get("/admin/sendmail/whoami")
async def sendmail_whoami(user: User | None = Depends(get_current_user)):
    """Lets the sendmail page check, before showing the form, whether the
    caller is an admin and what From address mail will go out as."""
    return {"is_admin": _is_admin(user), "from": settings.email_from}


@router.post("/admin/sendmail", response_model=SendMailResponse)
async def admin_sendmail(
    data: SendMailIn,
    user: User | None = Depends(get_current_user),
):
    """Send a one-off email AS the configured From address (hello@…), using
    the same EmailService/Resend backend the app uses for magic links.

    ADMIN-ONLY (settings.admin_user_ids). This is deliberately NOT public:
    an open "send email" endpoint is a spam relay that would get the domain
    blacklisted. Anonymous + logged-in-non-admin both get 403.
    """
    if not _is_admin(user):
        return JSONResponse(status_code=403, content={"detail": "forbidden"})
    try:
        result = await EmailService().send(EmailMessage(
            to=str(data.to),
            subject=data.subject,
            text=data.body,
            reply_to=(str(data.reply_to) if data.reply_to else None),
        ))
    except Exception as e:
        logger.warning("admin sendmail failed: %s", e)
        return JSONResponse(
            status_code=502,
            content={"ok": False, "detail": f"send failed: {e}"},
        )
    # In log-backend (dev) the send is a no-op stub; surface that honestly.
    backend = settings.email_backend
    return SendMailResponse(
        ok=True,
        detail=("sent via Resend" if backend == "resend" else f"backend={backend} (not actually emailed)"),
    )
