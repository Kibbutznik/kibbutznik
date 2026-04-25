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
async def test_webhook_handles_concurrent_dedupe_collision(client, db):
    """Race regression. Two concurrent webhooks with the same
    (event, idempotency_key) both pass `find_webhook` (because the
    dedupe row doesn't exist yet) and both reach the `mint`+
    `record_webhook` step. The unique index on (event,
    idempotency_key) makes the second `record_webhook` fail with
    IntegrityError.

    Pre-fix: that IntegrityError bubbled as 500 AND left the
    second mint committed — double-credit.

    Post-fix: the route catches IntegrityError, rolls back its own
    mint, and returns the WINNING request's ledger_entry_id with
    status='duplicate'.

    We simulate the race by pre-inserting the dedupe row before
    the request runs, so the route's record_webhook step is
    guaranteed to collide.
    """
    import uuid as _u
    from kbz.models.wallet import LedgerEntry, Wallet, WalletWebhookEvent
    from sqlalchemy import select
    cid = await _make_financial_community(client)

    # Hand-craft a "winning" mint that the simulated other request
    # already committed: a real ledger entry + the dedupe row
    # pointing at it. The idempotency_key matches what the
    # incoming request will use.
    # First seed the wallet so the ledger entry has a target.
    from kbz.models.wallet import OWNER_COMMUNITY
    wallet = Wallet(
        id=_u.uuid4(),
        owner_kind=OWNER_COMMUNITY,
        owner_id=_u.UUID(cid),
        balance="0",
    )
    db.add(wallet)
    winning_entry = LedgerEntry(
        id=_u.uuid4(),
        from_wallet=None,
        to_wallet=wallet.id,
        amount="50",
        proposal_id=None,
        external_ref="winning",
        webhook_event="test.seed",
        memo="winning earlier deposit",
    )
    db.add(winning_entry)
    db.add(WalletWebhookEvent(
        id=_u.uuid4(),
        event="test.seed",
        idempotency_key="race-key",
        ledger_entry_id=winning_entry.id,
    ))
    await db.commit()

    # Now this incoming request will: pass find_webhook (it sees
    # the existing row and short-circuits as duplicate). Wait —
    # that's the easy path. We need a path where find_webhook
    # MISSES the row. That happens when both requests land before
    # either's row has been committed.
    #
    # Easiest reproduction in a single-process test: monkeypatch
    # WalletService.find_webhook to return None on first call
    # only. Then the route proceeds to mint + record_webhook,
    # the IntegrityError fires, and the route's exception handler
    # should re-find the existing row and report 'duplicate'.
    from kbz.services.wallet_service import WalletService
    real_find = WalletService.find_webhook
    bypass_count = {"n": 0}

    async def _bypass_find_once(self, *, event, idempotency_key):
        bypass_count["n"] += 1
        if bypass_count["n"] == 1:
            return None
        return await real_find(self, event=event, idempotency_key=idempotency_key)

    WalletService.find_webhook = _bypass_find_once
    try:
        body = {
            "target_kind": "community",
            "target_id": cid,
            "amount": "50",
            "event": "test.seed",
            "external_ref": "loser",
            "idempotency_key": "race-key",
        }
        raw = json.dumps(body).encode()
        r = await client.post(
            "/webhooks/wallet-deposit",
            content=raw,
            headers={"X-KBZ-Signature": _sig(raw), "Content-Type": "application/json"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "duplicate"
        assert r.json()["ledger_entry_id"] == str(winning_entry.id)
    finally:
        WalletService.find_webhook = real_find


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
async def test_webhook_rejects_non_finite_amounts(client):
    """Decimal('Infinity') is > 0, so the old `amount <= 0` check
    passed it through straight to mint(). Reject Inf / -Inf / NaN
    as a class before they reach the ledger."""
    user = await create_test_user(client, name="inf-test")
    for bad in ("Infinity", "-Infinity", "NaN", "0", "-1"):
        body = {
            "target_kind": "user",
            "target_id": user["id"],
            "amount": bad,
            "event": "test.bad",
            "external_ref": f"ref-{bad}",
            "idempotency_key": f"idem-{bad}",
        }
        raw = json.dumps(body).encode()
        r = await client.post(
            "/webhooks/wallet-deposit",
            content=raw,
            headers={"X-KBZ-Signature": _sig(raw), "Content-Type": "application/json"},
        )
        assert r.status_code == 400, f"amount={bad!r} should 400, got {r.status_code}"


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
