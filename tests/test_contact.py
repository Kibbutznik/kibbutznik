"""Contact form (POST /contact, GET /admin/contact).

Store-first: a submission is persisted before any (best-effort) email,
so it's never lost. Anti-abuse: honeypot + per-IP rate limit + length
caps. Admin inbox is gated by admin_user_ids.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from kbz import config
from kbz.models.contact_message import ContactMessage


async def _count(db) -> int:
    return (await db.execute(select(func.count()).select_from(ContactMessage))).scalar_one()


@pytest.mark.asyncio
async def test_contact_happy_path_persists(client, db):
    resp = await client.post("/contact", json={
        "message": "Love this — how do I run my own kibbutz?",
        "name": "Dana",
        "email": "dana@example.com",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    rows = (await db.execute(select(ContactMessage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].message.startswith("Love this")
    assert rows[0].email == "dana@example.com"
    assert rows[0].name == "Dana"
    # ip captured server-side (127.0.0.1 under the test transport)
    assert rows[0].ip


@pytest.mark.asyncio
async def test_contact_anonymous_message_ok(client, db):
    """No name/email is fine — message is the only required field."""
    resp = await client.post("/contact", json={"message": "just a note"})
    assert resp.status_code == 200
    assert await _count(db) == 1


@pytest.mark.asyncio
async def test_contact_honeypot_silently_dropped(client, db):
    """A filled honeypot returns a cheerful 200 but persists nothing."""
    resp = await client.post("/contact", json={
        "message": "buy cheap stuff",
        "website": "http://spam.example",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert await _count(db) == 0


@pytest.mark.asyncio
async def test_contact_message_too_long_422(client, db):
    resp = await client.post("/contact", json={"message": "x" * 5001})
    assert resp.status_code == 422
    assert await _count(db) == 0


@pytest.mark.asyncio
async def test_contact_empty_message_422(client):
    resp = await client.post("/contact", json={"message": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_contact_bad_email_422(client):
    resp = await client.post("/contact", json={"message": "hi", "email": "not-an-email"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_contact_rate_limited_per_ip(client):
    """5/hour/IP — the 6th submission from the same IP gets 429."""
    for i in range(5):
        r = await client.post("/contact", json={"message": f"msg {i}"})
        assert r.status_code == 200, f"submission {i} should pass"
    r = await client.post("/contact", json={"message": "one too many"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


@pytest.mark.asyncio
async def test_admin_contact_locked_when_no_admins(client):
    r = await client.get("/admin/contact")
    assert r.status_code == 403
    assert "admin" in r.text.lower()


@pytest.mark.asyncio
async def test_admin_contact_403s_anonymous_even_with_admins(client, monkeypatch):
    monkeypatch.setattr(
        config.settings, "admin_user_ids",
        "11111111-1111-1111-1111-111111111111",
    )
    r = await client.get("/admin/contact")
    assert r.status_code == 403


# ── Admin sendmail gate ──────────────────────────────────

@pytest.mark.asyncio
async def test_sendmail_blocked_when_no_admins(client):
    r = await client.post("/admin/sendmail", json={
        "to": "x@example.com", "subject": "hi", "body": "yo",
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sendmail_blocked_anonymous_even_with_admins(client, monkeypatch):
    monkeypatch.setattr(
        config.settings, "admin_user_ids",
        "11111111-1111-1111-1111-111111111111",
    )
    r = await client.post("/admin/sendmail", json={
        "to": "x@example.com", "subject": "hi", "body": "yo",
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sendmail_whoami_reports_non_admin(client):
    r = await client.get("/admin/sendmail/whoami")
    assert r.status_code == 200
    assert r.json()["is_admin"] is False
