"""Webhook deposit — HMAC verification + idempotency + gating.

The webhook is the ONLY way credits enter the system from outside
Phase 1. So its guards (signature, idempotency, financial gate) are
the perimeter we care about here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest

from kbz.config import settings
from tests.conftest import create_test_user


_TEST_SECRET = "test-webhook-secret-abc123"


def _sig(body_bytes: bytes) -> str:
    mac = hmac.new(_TEST_SECRET.encode("utf-8"), body_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    """Every test in this module needs a secret set; the default is
    "" which disables the endpoint."""
    monkeypatch.setattr(settings, "webhook_secret", _TEST_SECRET)


async def _make_financial_community(client) -> str:
    founder = await create_test_user(client, name=f"f_{uuid.uuid4().hex[:6]}")
    r = await client.post(
        "/communities",
        json={
            "name": "Wh Test",
            "founder_user_id": founder["id"],
            "enable_financial": True,
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_webhook_rejects_unsigned(client):
    body = {
        "target_kind": "community",
        "target_id": str(uuid.uuid4()),
        "amount": "50",
        "event": "test.seed",
        "external_ref": "r1",
        "idempotency_key": "k1",
    }
    r = await client.post("/webhooks/wallet-deposit", json=body)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature(client):
    body = {
        "target_kind": "community",
        "target_id": str(uuid.uuid4()),
        "amount": "50",
        "event": "test.seed",
        "external_ref": "r1",
        "idempotency_key": "k1",
    }
    r = await client.post(
        "/webhooks/wallet-deposit",
        content=json.dumps(body).encode(),
        headers={"X-KBZ-Signature": "sha256=0000", "Content-Type": "application/json"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_credits_financial_community(client):
    cid = await _make_financial_community(client)
    body = {
        "target_kind": "community",
        "target_id": cid,
        "amount": "200",
        "event": "test.seed",
        "external_ref": "ref-1",
        "idempotency_key": "idem-1",
    }
    raw = json.dumps(body).encode()
    r = await client.post(
        "/webhooks/wallet-deposit",
        content=raw,
        headers={
            "X-KBZ-Signature": _sig(raw),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "credited"

    w = await client.get(f"/communities/{cid}/wallet")
    assert w.json()["balance"].startswith("200")


@pytest.mark.asyncio
async def test_webhook_is_idempotent_on_same_key(client):
    cid = await _make_financial_community(client)
    body = {
        "target_kind": "community",
        "target_id": cid,
        "amount": "75",
        "event": "test.seed",
        "external_ref": "once",
        "idempotency_key": "only-once",
    }
    raw = json.dumps(body).encode()
    headers = {
        "X-KBZ-Signature": _sig(raw),
        "Content-Type": "application/json",
    }
    r1 = await client.post("/webhooks/wallet-deposit", content=raw, headers=headers)
    r2 = await client.post("/webhooks/wallet-deposit", content=raw, headers=headers)
    assert r1.json()["status"] == "credited"
    assert r2.json()["status"] == "duplicate"
    # Balance didn't double
    w = await client.get(f"/communities/{cid}/wallet")
    assert w.json()["balance"].startswith("75")


@pytest.mark.asyncio
async def test_webhook_404s_on_non_financial_community(client):
    # Community with Financial=false
    founder = await create_test_user(client, name="non-fin")
    r = await client.post(
        "/communities",
        json={"name": "x", "founder_user_id": founder["id"]},
    )
    cid = r.json()["id"]
    body = {
        "target_kind": "community",
        "target_id": cid,
        "amount": "10",
        "event": "test.seed",
        "external_ref": "r",
        "idempotency_key": "k",
    }
    raw = json.dumps(body).encode()
    r = await client.post(
        "/webhooks/wallet-deposit",
        content=raw,
        headers={"X-KBZ-Signature": _sig(raw), "Content-Type": "application/json"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_webhook_user_target_works_without_financial_community(client):
    """User wallets are platform-wide, so webhook deposits to users
    succeed regardless of community state."""
    user = await create_test_user(client, name="recipient")
    body = {
        "target_kind": "user",
        "target_id": user["id"],
        "amount": "42",
        "event": "external.grant",
        "external_ref": "grant-1",
        "idempotency_key": "idem-u1",
    }
    raw = json.dumps(body).encode()
    r = await client.post(
        "/webhooks/wallet-deposit",
        content=raw,
        headers={"X-KBZ-Signature": _sig(raw), "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "credited"


@pytest.mark.asyncio
async def test_webhook_503_when_secret_unset(client, monkeypatch):
    # Override the autouse fixture for this test
    monkeypatch.setattr(settings, "webhook_secret", "")
    body = {
        "target_kind": "user",
        "target_id": str(uuid.uuid4()),
        "amount": "1",
        "event": "x",
        "external_ref": "r",
        "idempotency_key": "k",
    }
    raw = json.dumps(body).encode()
    r = await client.post(
        "/webhooks/wallet-deposit",
        content=raw,
        headers={"X-KBZ-Signature": _sig(raw), "Content-Type": "application/json"},
    )
    assert r.status_code == 503
