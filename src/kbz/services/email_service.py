"""Email sending — one interface, two backends.

Callers only ever touch `EmailService`. Which backend they get is decided
by config:

  KBZ_EMAIL_BACKEND=log      → dev-only; logs to console AND writes the
                                full message to an email_outbox in-memory
                                list so tests and local dev can inspect it
  KBZ_EMAIL_BACKEND=resend   → real send via Resend's HTTP API

Why Resend (and not SendGrid / SES / Postmark):
  - 3000 emails/mo free (100/day) — generous for an MVP
  - Modern JSON API, no SMTP headache
  - DKIM/SPF via DNS on a custom domain — same hygiene as any sender
  - Dead simple: one POST to https://api.resend.com/emails

If KBZ_RESEND_API_KEY is absent the service downgrades to the log
backend automatically with a warning — so missing config never breaks
an endpoint. The email just won't leave the box.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

import httpx

from kbz.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    to: str
    subject: str
    # Plain-text body. HTML is optional — Resend will generate a
    # plain-text part from the HTML if only html is supplied, but we
    # always send both when we have both so spam filters stay happy.
    text: str
    html: str | None = None
    from_email: str | None = None  # falls back to settings.email_from
    reply_to: str | None = None


class EmailBackend(ABC):
    @abstractmethod
    async def send(self, msg: EmailMessage) -> dict:
        """Send an email. Returns provider response (or a stub dict in log
        mode). Raises on unrecoverable errors; callers should catch and
        degrade gracefully — most email failures shouldn't fail the user
        action that triggered them (e.g. a magic-link request).
        """


class LogBackend(EmailBackend):
    """No-op backend for tests and dev mode. Records every message so
    callers can inspect what would have been sent.
    """

    # Class-level so tests can peek without holding a reference to the
    # service instance. Treat as ephemeral; trimmed on `clear()`.
    outbox: ClassVar[list[EmailMessage]] = []

    async def send(self, msg: EmailMessage) -> dict:
        LogBackend.outbox.append(msg)
        logger.info(
            "[EmailService:log] would send to=%s subject=%r (text=%d chars)",
            msg.to, msg.subject, len(msg.text),
        )
        return {"id": f"log_{len(LogBackend.outbox)}", "backend": "log"}

    @classmethod
    def clear(cls) -> None:
        cls.outbox.clear()


class ResendBackend(EmailBackend):
    """Real send via Resend's HTTP API.

    Resend's /emails endpoint accepts:
      {
        "from": "You <you@domain.com>",
        "to": ["recipient@example.com"],
        "subject": "...",
        "text": "...",
        "html": "..."
      }
    It returns {"id": "re_abc123..."} on success or an error envelope.
    """

    API_URL: ClassVar[str] = "https://api.resend.com/emails"

    def __init__(self, api_key: str, default_from: str):
        self._api_key = api_key
        self._default_from = default_from
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send(self, msg: EmailMessage) -> dict:
        payload = {
            "from": msg.from_email or self._default_from,
            "to": [msg.to],
            "subject": msg.subject,
            "text": msg.text,
        }
        if msg.html:
            payload["html"] = msg.html
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        try:
            resp = await self._client.post(
                self.API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            if resp.status_code >= 400:
                logger.warning(
                    "[EmailService:resend] send failed to=%s status=%d body=%s",
                    msg.to, resp.status_code, resp.text[:300],
                )
                # Return the error shape, don't raise — email failures
                # shouldn't 500 the user's request.
                return {"error": resp.text, "status": resp.status_code}
            body = resp.json()
            logger.info(
                "[EmailService:resend] sent id=%s to=%s",
                body.get("id"), msg.to,
            )
            return body
        except Exception as e:
            logger.warning("[EmailService:resend] exception to=%s: %s", msg.to, e)
            return {"error": str(e)}


class EmailService:
    """The thing callers use. Pick a backend per the config."""

    def __init__(self, backend: EmailBackend | None = None):
        self.backend = backend or self._resolve_backend()

    @staticmethod
    def _resolve_backend() -> EmailBackend:
        chosen = (settings.email_backend or "log").lower().strip()
        if chosen == "resend":
            api_key = settings.resend_api_key
            if not api_key:
                logger.warning(
                    "[EmailService] KBZ_EMAIL_BACKEND=resend but "
                    "KBZ_RESEND_API_KEY is empty — downgrading to log backend."
                )
                return LogBackend()
            return ResendBackend(
                api_key=api_key,
                default_from=settings.email_from,
            )
        return LogBackend()

    async def send(self, msg: EmailMessage) -> dict:
        return await self.backend.send(msg)


# ── Ready-made message templates ────────────────────────────────────
# Keep these as small formatter functions (not classes) so they're easy
# to override per-environment / locale later.

def render_magic_link_email(*, verify_url: str, app_name: str = "KBZ") -> EmailMessage:
    """A bare-bones magic-link message. Template deliberately spartan;
    richer HTML can come when the product has more brand."""
    subject = f"Your {app_name} sign-in link"
    text = (
        f"Click to sign in to {app_name}:\n\n"
        f"  {verify_url}\n\n"
        "This link is single-use and expires in 15 minutes.\n"
        "If you didn't request it, ignore this email."
    )
    html = (
        f"<p>Click to sign in to <strong>{app_name}</strong>:</p>"
        f'<p><a href="{verify_url}">{verify_url}</a></p>'
        "<p style=\"color:#666;font-size:.9em\">"
        "This link is single-use and expires in 15 minutes. "
        "If you didn't request it, you can ignore this email."
        "</p>"
    )
    return EmailMessage(to="", subject=subject, text=text, html=html)


def render_invite_email(
    *,
    invite_url: str,
    inviter_name: str,
    community_name: str,
    app_name: str = "KBZ",
) -> EmailMessage:
    """An invite handoff. The recipient clicks through, enters their
    email, and the claim flow files a Membership proposal."""
    subject = f"{inviter_name} invited you to {community_name}"
    text = (
        f"{inviter_name} invited you to join the {app_name} community "
        f"\"{community_name}\".\n\n"
        f"Open this link to accept:\n  {invite_url}\n\n"
        "Your membership goes through a community vote — existing "
        "members decide whether to admit you."
    )
    html = (
        f"<p><strong>{inviter_name}</strong> invited you to join the "
        f"{app_name} community <em>{community_name}</em>.</p>"
        f'<p><a href="{invite_url}">Open invitation</a></p>'
        "<p style=\"color:#666;font-size:.9em\">"
        "Your membership goes through a community vote — existing members "
        "decide whether to admit you."
        "</p>"
    )
    return EmailMessage(to="", subject=subject, text=text, html=html)
