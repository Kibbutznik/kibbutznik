"""EmailService tests.

Cover three things:

1. The log backend captures messages (used in dev + tests so callers
   can inspect outbound mail).
2. The auto-downgrade behavior — requesting `resend` without an API
   key quietly falls back to log, so a missing secret never 500s.
3. Templates render with the expected strings so a copy tweak doesn't
   silently break the magic-link UX.
"""

from __future__ import annotations

import pytest

from kbz.services.email_service import (
    EmailMessage,
    EmailService,
    LogBackend,
    render_invite_email,
    render_magic_link_email,
)


@pytest.mark.asyncio
async def test_log_backend_captures_message():
    LogBackend.clear()
    svc = EmailService(backend=LogBackend())
    result = await svc.send(
        EmailMessage(
            to="alice@example.com",
            subject="Hello",
            text="World",
        )
    )
    assert result["backend"] == "log"
    assert len(LogBackend.outbox) == 1
    captured = LogBackend.outbox[0]
    assert captured.to == "alice@example.com"
    assert captured.subject == "Hello"
    assert captured.text == "World"


@pytest.mark.asyncio
async def test_service_downgrades_when_resend_key_missing(monkeypatch):
    """If KBZ_EMAIL_BACKEND=resend but no API key, we silently use log."""
    from kbz.config import settings

    monkeypatch.setattr(settings, "email_backend", "resend")
    monkeypatch.setattr(settings, "resend_api_key", "")
    # Don't pass backend explicitly → service resolves from config
    svc = EmailService()
    assert isinstance(svc.backend, LogBackend)


@pytest.mark.asyncio
async def test_service_defaults_to_log_backend(monkeypatch):
    from kbz.config import settings

    monkeypatch.setattr(settings, "email_backend", "log")
    svc = EmailService()
    assert isinstance(svc.backend, LogBackend)


def test_magic_link_template_has_url_and_warning():
    msg = render_magic_link_email(verify_url="https://example.com/auth/verify?token=abc")
    assert "/auth/verify?token=abc" in msg.text
    assert "/auth/verify?token=abc" in (msg.html or "")
    # Must warn that the link is one-shot
    assert "single-use" in msg.text
    # Must be a non-empty subject
    assert msg.subject


def test_invite_template_renders_inviter_and_community():
    msg = render_invite_email(
        invite_url="https://example.com/app/#/invite/xyz",
        inviter_name="Dana",
        community_name="Ledger Analysts",
    )
    assert "Dana" in msg.text
    assert "Ledger Analysts" in msg.text
    assert "/app/#/invite/xyz" in (msg.html or "")


@pytest.mark.asyncio
async def test_magic_link_request_hits_email_service(client):
    """End-to-end: POST /auth/request-magic-link must deposit a message
    in the log backend (proves the router wires into EmailService)."""
    LogBackend.clear()
    r = await client.post(
        "/auth/request-magic-link", json={"email": "target@example.com"}
    )
    assert r.status_code == 200
    # One message landed in the outbox, addressed to the recipient
    assert len(LogBackend.outbox) == 1
    captured = LogBackend.outbox[0]
    assert captured.to == "target@example.com"
    assert "/auth/verify?token=" in captured.text
