"""Escrow lifecycle — open on Membership create, release on accept,
refund on reject/cancel/withdraw."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kbz.models.community import Community
from kbz.models.variable import Variable
from kbz.models.wallet import (
    OWNER_COMMUNITY,
    OWNER_ESCROW,
    OWNER_USER,
    Wallet,
)
from kbz.services.wallet_service import WalletService


@pytest.fixture
def sf(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_financial_community(db) -> uuid.UUID:
    cid = uuid.uuid4()
    db.add(
        Community(
            id=cid,
            parent_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            name="Escrow Test",
            status=1,
            member_count=1,
        )
    )
    db.add(Variable(community_id=cid, name="Financial", value="internal"))
    await db.flush()
    return cid


async def _fund_user(svc: WalletService, user_id, amount) -> Wallet:
    w = await svc.get_or_create(OWNER_USER, user_id, gate=False)
    await svc.mint(
        w, amount, webhook_event="welcome.signup",
        external_ref=f"welcome:{user_id}",
    )
    return w


# ── Open ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escrow_open_debits_user_and_parks_in_escrow(sf):
    async with sf() as db:
        await _seed_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        user_id = uuid.uuid4()
        user_wallet = await _fund_user(svc, user_id, "100")
        await db.commit()

        proposal_id = uuid.uuid4()
        escrow = await svc.escrow_open(proposal_id, "20", user_wallet)
        await db.commit()

        user_wallet = (
            await db.execute(select(Wallet).where(Wallet.id == user_wallet.id))
        ).scalar_one()
        escrow = (
            await db.execute(select(Wallet).where(Wallet.id == escrow.id))
        ).scalar_one()
        assert user_wallet.balance == Decimal("80")
        assert escrow.balance == Decimal("20")
        assert escrow.owner_kind == OWNER_ESCROW
        assert escrow.owner_id == proposal_id


# ── Release ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escrow_release_moves_to_community(sf):
    async with sf() as db:
        cid = await _seed_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        user_id = uuid.uuid4()
        user_wallet = await _fund_user(svc, user_id, "100")
        community_wallet = await svc.get_or_create(OWNER_COMMUNITY, cid)
        await db.commit()
        pid = uuid.uuid4()
        await svc.escrow_open(pid, "20", user_wallet)
        await db.commit()

        await svc.escrow_release(pid, community_wallet)
        await db.commit()

        # Escrow wallet is gone
        escrow = (
            await db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_ESCROW,
                    Wallet.owner_id == pid,
                )
            )
        ).scalar_one_or_none()
        assert escrow is None

        community_wallet = (
            await db.execute(select(Wallet).where(Wallet.id == community_wallet.id))
        ).scalar_one()
        user_wallet = (
            await db.execute(select(Wallet).where(Wallet.id == user_wallet.id))
        ).scalar_one()
        assert community_wallet.balance == Decimal("20")
        assert user_wallet.balance == Decimal("80")  # stayed debited


# ── Refund ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escrow_refund_returns_to_applicant(sf):
    async with sf() as db:
        await _seed_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        user_id = uuid.uuid4()
        user_wallet = await _fund_user(svc, user_id, "50")
        await db.commit()
        pid = uuid.uuid4()
        await svc.escrow_open(pid, "15", user_wallet)
        await db.commit()

        await svc.escrow_refund(pid)
        await db.commit()

        user_wallet = (
            await db.execute(select(Wallet).where(Wallet.id == user_wallet.id))
        ).scalar_one()
        assert user_wallet.balance == Decimal("50")  # full refund
        escrow = (
            await db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_ESCROW, Wallet.owner_id == pid,
                )
            )
        ).scalar_one_or_none()
        assert escrow is None


# ── Edge cases ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_release_no_escrow_is_noop(sf):
    async with sf() as db:
        cid = await _seed_financial_community(db)
        await db.commit()
        svc = WalletService(db)
        cw = await svc.get_or_create(OWNER_COMMUNITY, cid)
        result = await svc.escrow_release(uuid.uuid4(), cw)
        assert result is None


@pytest.mark.asyncio
async def test_refund_no_escrow_is_noop(sf):
    async with sf() as db:
        result = await WalletService(db).escrow_refund(uuid.uuid4())
        assert result is None
