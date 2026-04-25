"""WalletService primitives — pure money-movement, no routes.

Uses the test DB's own engine fixture; everything runs in one
session. Asserts the invariants: non-negative balance, proper
authorization (proposal_id OR external_ref), ledger==balance.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kbz.models.community import Community
from kbz.models.variable import Variable
from kbz.models.wallet import (
    OWNER_COMMUNITY,
    OWNER_USER,
    LedgerEntry,
    Wallet,
)
from kbz.services.wallet_service import (
    FinancialModuleDisabledError,
    InsufficientFundsError,
    WalletService,
    _parse_amount,
)


@pytest.fixture
def sf(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


async def _make_financial_community(db, *, name="C") -> uuid.UUID:
    """Create a community row directly + its Financial=internal var."""
    cid = uuid.uuid4()
    db.add(
        Community(
            id=cid,
            parent_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            name=name,
            status=1,
            member_count=1,
        )
    )
    db.add(Variable(community_id=cid, name="Financial", value="internal"))
    await db.flush()
    return cid


async def _make_non_financial_community(db, *, name="NonFin") -> uuid.UUID:
    cid = uuid.uuid4()
    db.add(
        Community(
            id=cid,
            parent_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            name=name,
            status=1,
            member_count=1,
        )
    )
    db.add(Variable(community_id=cid, name="Financial", value="false"))
    await db.flush()
    return cid


# ── _parse_amount — tiny pure function ────────────────────────────

def test_parse_amount_accepts_common_numerics():
    assert _parse_amount("10") == Decimal("10")
    assert _parse_amount("10.5") == Decimal("10.500000")
    assert _parse_amount(7) == Decimal("7")
    assert _parse_amount(Decimal("0.000001")) == Decimal("0.000001")


def test_parse_amount_rejects_bad_inputs():
    for bad in (None, "abc", "", "-1", 0, -5, float("inf"), float("nan")):
        with pytest.raises((ValueError, TypeError)):
            _parse_amount(bad)


# ── is_financial — module gate ────────────────────────────────────

@pytest.mark.asyncio
async def test_is_financial_true_when_variable_is_internal(sf):
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()
        assert await WalletService(db).is_financial(cid)


@pytest.mark.asyncio
async def test_is_financial_false_when_variable_missing_or_off(sf):
    async with sf() as db:
        c1 = uuid.uuid4()
        db.add(Community(id=c1, parent_id=uuid.UUID(int=0), name="X", status=1, member_count=1))
        # No Financial variable row at all
        await db.commit()
        svc = WalletService(db)
        assert await svc.is_financial(c1) is False

        c2 = await _make_non_financial_community(db, name="Off")
        await db.commit()
        assert await svc.is_financial(c2) is False


# ── get_or_create — financial gate ────────────────────────────────

@pytest.mark.asyncio
async def test_get_or_create_gates_community_wallet(sf):
    async with sf() as db:
        cid = await _make_non_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        with pytest.raises(FinancialModuleDisabledError):
            await svc.get_or_create(OWNER_COMMUNITY, cid)


@pytest.mark.asyncio
async def test_get_or_create_community_wallet_when_financial(sf):
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, cid)
        await db.commit()
        assert w.balance == Decimal("0")
        assert w.owner_kind == OWNER_COMMUNITY
        assert w.owner_id == cid

        # idempotent — second call returns same row
        w2 = await svc.get_or_create(OWNER_COMMUNITY, cid)
        assert w.id == w2.id


@pytest.mark.asyncio
async def test_get_or_create_race_returns_winner_not_500(sf):
    """Two concurrent first-access requests for the same wallet both
    pass the SELECT before either flushes. The unique index on
    (owner_kind, owner_id) catches the loser's INSERT — pre-fix the
    loser surfaced as a raw IntegrityError → 500. Now the service
    catches it, rolls back, and returns the winner's row.

    Repro deterministically by inserting a wallet row out-of-band
    THEN calling get_or_create with monkey-patched SELECT to skip
    the dedupe check and force the IntegrityError.
    """
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()

    # First request lands the row.
    async with sf() as db:
        await WalletService(db).get_or_create(OWNER_COMMUNITY, cid)
        await db.commit()

    # Second request: monkey-patch the dedupe SELECT to return None
    # so we proceed to INSERT and trigger the unique-index violation.
    from unittest.mock import patch

    async with sf() as db:
        svc = WalletService(db)
        original_execute = db.execute

        async def fake_execute(stmt, *args, **kwargs):
            sql = str(stmt).lower()
            # Skip the first dedupe SELECT (looks for an existing wallet)
            # by returning a fake "no rows" result. Other queries pass through.
            if "select" in sql and "wallets" in sql and "owner_kind" in sql:
                class _Empty:
                    def scalar_one_or_none(self_inner): return None
                    def scalar_one(self_inner): return None
                    def all(self_inner): return []
                    def scalars(self_inner): return self_inner
                # Restore execute for subsequent calls — we only want to
                # short-circuit the FIRST dedupe SELECT.
                db.execute = original_execute
                return _Empty()
            return await original_execute(stmt, *args, **kwargs)

        with patch.object(db, "execute", side_effect=fake_execute):
            winner = await svc.get_or_create(OWNER_COMMUNITY, cid)

        assert winner is not None
        assert winner.owner_kind == OWNER_COMMUNITY
        assert winner.owner_id == cid


@pytest.mark.asyncio
async def test_user_wallets_bypass_financial_gate(sf):
    async with sf() as db:
        # No community at all — user wallets are platform-wide.
        user_id = uuid.uuid4()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_USER, user_id, gate=False)
        await db.commit()
        assert w.balance == Decimal("0")


# ── mint / burn / transfer ────────────────────────────────────────

@pytest.mark.asyncio
async def test_mint_adds_balance_and_writes_ledger(sf):
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, cid)
        entry = await svc.mint(
            w, "50", webhook_event="test.seed",
            external_ref="test-ref-1", memo="unit-test",
        )
        await db.commit()
        # Re-fetch to see denormalized balance
        w = (await db.execute(select(Wallet).where(Wallet.id == w.id))).scalar_one()
        assert w.balance == Decimal("50")
        assert entry.amount == Decimal("50")
        assert entry.to_wallet == w.id
        assert entry.from_wallet is None
        assert entry.webhook_event == "test.seed"


@pytest.mark.asyncio
async def test_burn_removes_balance_and_requires_proposal(sf):
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, cid)
        await svc.mint(w, "100", webhook_event="test", external_ref="r")
        await db.commit()

        pid = uuid.uuid4()
        entry = await svc.burn(w, "40", proposal_id=pid, memo="payout")
        await db.commit()
        w = (await db.execute(select(Wallet).where(Wallet.id == w.id))).scalar_one()
        assert w.balance == Decimal("60")
        assert entry.proposal_id == pid
        assert entry.from_wallet == w.id


@pytest.mark.asyncio
async def test_burn_refuses_insufficient(sf):
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, cid)
        await svc.mint(w, "10", webhook_event="test", external_ref="r")
        await db.commit()
        with pytest.raises(InsufficientFundsError):
            await svc.burn(w, "50", proposal_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_transfer_between_wallets(sf):
    async with sf() as db:
        c1 = await _make_financial_community(db, name="src")
        c2 = await _make_financial_community(db, name="dst")
        await db.commit()
        svc = WalletService(db)
        src = await svc.get_or_create(OWNER_COMMUNITY, c1)
        dst = await svc.get_or_create(OWNER_COMMUNITY, c2)
        await svc.mint(src, "100", webhook_event="test", external_ref="r")
        await db.commit()

        pid = uuid.uuid4()
        await svc.transfer(src, dst, "30", proposal_id=pid, memo="split")
        await db.commit()

        src = (await db.execute(select(Wallet).where(Wallet.id == src.id))).scalar_one()
        dst = (await db.execute(select(Wallet).where(Wallet.id == dst.id))).scalar_one()
        assert src.balance == Decimal("70")
        assert dst.balance == Decimal("30")


@pytest.mark.asyncio
async def test_transfer_requires_proposal_id(sf):
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        w1 = await svc.get_or_create(OWNER_COMMUNITY, cid)
        w2 = await svc.get_or_create(OWNER_USER, uuid.uuid4(), gate=False)
        await svc.mint(w1, "5", webhook_event="test", external_ref="r")
        await db.commit()
        with pytest.raises(ValueError):
            await svc.transfer(w1, w2, "3")


# ── Invariant: balance matches SUM(ledger_entries) ────────────────

@pytest.mark.asyncio
async def test_balance_matches_ledger_sum(sf):
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, cid)
        pid = uuid.uuid4()
        # Mint 100, burn 30, mint 20  → balance 90
        await svc.mint(w, "100", webhook_event="a", external_ref="r1")
        await svc.burn(w, "30", proposal_id=pid)
        await svc.mint(w, "20", webhook_event="b", external_ref="r2")
        await db.commit()

        # Denormalized balance
        w = (await db.execute(select(Wallet).where(Wallet.id == w.id))).scalar_one()
        # Recompute from ledger
        entries = (
            await db.execute(
                select(LedgerEntry).where(
                    (LedgerEntry.from_wallet == w.id) | (LedgerEntry.to_wallet == w.id)
                )
            )
        ).scalars().all()
        reconstructed = sum(
            (e.amount if e.to_wallet == w.id else -e.amount for e in entries),
            Decimal("0"),
        )
        assert w.balance == reconstructed == Decimal("90")


# ── Schema-level CHECK enforces non-negative balance ─────────────

@pytest.mark.asyncio
async def test_cannot_insert_wallet_with_negative_balance(sf):
    async with sf() as db:
        cid = await _make_financial_community(db)
        await db.commit()
        db.add(
            Wallet(
                id=uuid.uuid4(),
                owner_kind=OWNER_COMMUNITY,
                owner_id=cid,   # different from the one we'd create above
                balance=Decimal("-1"),
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
