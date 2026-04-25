"""ExecutionService economy handlers — funding / payment / payback /
dividend / end-action sweep / membership escrow release.

Exercises the in-memory paths only; routes are covered separately
by tests/test_wallets_router.py. The goal here is to prove that an
accepted proposal causes the ledger movement the plan promised.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kbz.enums import CommunityStatus, ProposalStatus, ProposalType
from kbz.models.action import Action
from kbz.models.community import Community
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.variable import Variable
from kbz.models.wallet import (
    OWNER_ACTION,
    OWNER_COMMUNITY,
    OWNER_USER,
    Wallet,
)
from kbz.services.execution_service import ExecutionService
from kbz.services.wallet_service import WalletService


@pytest.fixture
def sf(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


async def _mk_community(db, *, financial: bool, name: str) -> uuid.UUID:
    cid = uuid.uuid4()
    db.add(
        Community(
            id=cid,
            parent_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            name=name,
            status=CommunityStatus.ACTIVE,
            member_count=1,
        )
    )
    db.add(
        Variable(
            community_id=cid,
            name="Financial",
            value="internal" if financial else "false",
        )
    )
    await db.flush()
    return cid


async def _mk_action(db, parent_community_id: uuid.UUID) -> uuid.UUID:
    """Creates an action AND its sub-community (actions are conceptually
    paired with a sub-community in KBZ). For our handlers we only need
    the Action row + the sub-community + its Financial inheritance."""
    action_id = uuid.uuid4()
    db.add(
        Action(
            action_id=action_id,
            parent_community_id=parent_community_id,
            status=CommunityStatus.ACTIVE,
        )
    )
    # The action's "inner" community — inherits the parent's Financial
    # setting since wallet routing looks up via Action.parent_community_id.
    db.add(
        Community(
            id=action_id,
            parent_id=parent_community_id,
            name="sub",
            status=CommunityStatus.ACTIVE,
            member_count=1,
        )
    )
    await db.flush()
    return action_id


async def _mk_proposal(
    db, *, community_id, user_id, ptype, val_text=None, val_uuid=None,
) -> Proposal:
    p = Proposal(
        id=uuid.uuid4(),
        community_id=community_id,
        user_id=user_id,
        proposal_type=ptype,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="test",
        val_text=val_text,
        val_uuid=val_uuid,
        age=0,
        support_count=0,
    )
    db.add(p)
    await db.flush()
    return p


# ── Funding: parent community → child action ───────────────────────

@pytest.mark.asyncio
async def test_funding_handler_moves_parent_to_child(sf):
    async with sf() as db:
        parent = await _mk_community(db, financial=True, name="parent")
        action = await _mk_action(db, parent)
        await db.commit()

        svc = WalletService(db)
        parent_w = await svc.get_or_create(OWNER_COMMUNITY, parent)
        await svc.mint(parent_w, "100", webhook_event="seed", external_ref="r")
        await db.commit()

        prop = await _mk_proposal(
            db,
            community_id=parent,
            user_id=uuid.uuid4(),
            ptype=ProposalType.FUNDING,
            val_uuid=action,
            val_text="30",
        )
        await db.commit()

        await ExecutionService(db)._exec_funding(prop)
        await db.commit()

        parent_w = (await db.execute(select(Wallet).where(Wallet.id == parent_w.id))).scalar_one()
        action_w = (
            await db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_ACTION, Wallet.owner_id == action,
                )
            )
        ).scalar_one()
        assert parent_w.balance == Decimal("70")
        assert action_w.balance == Decimal("30")


@pytest.mark.asyncio
async def test_dividend_refuses_non_finite_amount(sf):
    """An accepted Dividend with val_text='Infinity' must NOT
    crash the executor. Decimal('Infinity').quantize(...) raises
    InvalidOperation which would otherwise bubble up through
    pulse_service.execute_pulse and corrupt pulse processing.
    The handler must short-circuit on non-finite amounts."""
    from kbz.enums import MemberStatus
    async with sf() as db:
        cid = await _mk_community(db, financial=True, name="div-inf")
        # Need at least one active member so the early-return on
        # `if not members` doesn't mask the real bug.
        member_id = uuid.uuid4()
        db.add(Member(
            community_id=cid,
            user_id=member_id,
            status=MemberStatus.ACTIVE,
            seniority=0,
        ))
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, cid)
        await svc.mint(w, "100", webhook_event="seed", external_ref="r")
        await db.commit()

        prop = await _mk_proposal(
            db,
            community_id=cid,
            user_id=uuid.uuid4(),
            ptype=ProposalType.DIVIDEND,
            val_text="Infinity",
        )
        await db.commit()

        # Without the fix: raises InvalidOperation. With it: returns
        # cleanly and the wallet stays untouched.
        await ExecutionService(db)._exec_dividend(prop)
        await db.commit()
        w = (await db.execute(select(Wallet).where(Wallet.id == w.id))).scalar_one()
        assert w.balance == Decimal("100")  # untouched


@pytest.mark.asyncio
async def test_funding_short_circuits_when_community_not_financial(sf):
    async with sf() as db:
        parent = await _mk_community(db, financial=False, name="not-fin")
        action = await _mk_action(db, parent)
        await db.commit()

        prop = await _mk_proposal(
            db,
            community_id=parent,
            user_id=uuid.uuid4(),
            ptype=ProposalType.FUNDING,
            val_uuid=action,
            val_text="30",
        )
        await db.commit()

        await ExecutionService(db)._exec_funding(prop)
        await db.commit()
        # No wallets created — financial gate kept it silent.
        count = (await db.execute(select(Wallet))).scalars().all()
        assert len(count) == 0


# ── Payment: leaf burns from its own wallet ────────────────────────

@pytest.mark.asyncio
async def test_payment_handler_burns_from_leaf_community(sf):
    async with sf() as db:
        cid = await _mk_community(db, financial=True, name="leaf")
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, cid)
        await svc.mint(w, "50", webhook_event="seed", external_ref="r")
        await db.commit()

        prop = await _mk_proposal(
            db,
            community_id=cid,
            user_id=uuid.uuid4(),
            ptype=ProposalType.PAYMENT,
            val_text="20",
        )
        await db.commit()

        await ExecutionService(db)._exec_payment(prop)
        await db.commit()
        w = (await db.execute(select(Wallet).where(Wallet.id == w.id))).scalar_one()
        assert w.balance == Decimal("30")


@pytest.mark.asyncio
async def test_payment_refused_when_community_has_children(sf):
    async with sf() as db:
        parent = await _mk_community(db, financial=True, name="not-leaf")
        await _mk_action(db, parent)   # gives it a child
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, parent)
        await svc.mint(w, "50", webhook_event="seed", external_ref="r")
        await db.commit()

        prop = await _mk_proposal(
            db,
            community_id=parent,
            user_id=uuid.uuid4(),
            ptype=ProposalType.PAYMENT,
            val_text="20",
        )
        await db.commit()

        await ExecutionService(db)._exec_payment(prop)
        await db.commit()
        w = (await db.execute(select(Wallet).where(Wallet.id == w.id))).scalar_one()
        assert w.balance == Decimal("50")  # untouched


# ── PayBack: inverse of payment → mint into community ──────────────

@pytest.mark.asyncio
async def test_payback_mints_into_community(sf):
    async with sf() as db:
        cid = await _mk_community(db, financial=True, name="pb")
        await db.commit()
        svc = WalletService(db)
        await svc.get_or_create(OWNER_COMMUNITY, cid)
        await db.commit()

        prop = await _mk_proposal(
            db,
            community_id=cid,
            user_id=uuid.uuid4(),
            ptype=ProposalType.PAY_BACK,
            val_text="15",
        )
        await db.commit()

        await ExecutionService(db)._exec_pay_back(prop)
        await db.commit()
        w = (
            await db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_COMMUNITY, Wallet.owner_id == cid,
                )
            )
        ).scalar_one()
        assert w.balance == Decimal("15")


# ── Dividend: split community balance across members ───────────────

@pytest.mark.asyncio
async def test_dividend_splits_evenly_across_members(sf):
    async with sf() as db:
        cid = await _mk_community(db, financial=True, name="div")
        await db.commit()
        svc = WalletService(db)
        w = await svc.get_or_create(OWNER_COMMUNITY, cid)
        await svc.mint(w, "100", webhook_event="seed", external_ref="r")

        u1, u2 = uuid.uuid4(), uuid.uuid4()
        db.add(Member(community_id=cid, user_id=u1, status=1, seniority=0))
        db.add(Member(community_id=cid, user_id=u2, status=1, seniority=0))
        await db.commit()

        prop = await _mk_proposal(
            db,
            community_id=cid,
            user_id=uuid.uuid4(),
            ptype=ProposalType.DIVIDEND,
            val_text="60",
        )
        await db.commit()
        await ExecutionService(db)._exec_dividend(prop)
        await db.commit()

        u1_w = (
            await db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_USER, Wallet.owner_id == u1,
                )
            )
        ).scalar_one()
        u2_w = (
            await db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_USER, Wallet.owner_id == u2,
                )
            )
        ).scalar_one()
        c_w = (await db.execute(select(Wallet).where(Wallet.id == w.id))).scalar_one()
        # 60 / 2 = 30 each
        assert u1_w.balance == Decimal("30")
        assert u2_w.balance == Decimal("30")
        assert c_w.balance == Decimal("40")  # 100 - 60


# ── EndAction: sweep action balance back to parent ─────────────────

@pytest.mark.asyncio
async def test_end_action_sweeps_balance_to_parent(sf):
    async with sf() as db:
        parent = await _mk_community(db, financial=True, name="parent")
        action = await _mk_action(db, parent)
        await db.commit()

        svc = WalletService(db)
        a_w = await svc.get_or_create(OWNER_ACTION, action)
        await svc.mint(a_w, "25", webhook_event="seed", external_ref="r")
        await db.commit()

        prop = await _mk_proposal(
            db,
            community_id=parent,
            user_id=uuid.uuid4(),
            ptype=ProposalType.END_ACTION,
            val_uuid=action,
        )
        await db.commit()
        await ExecutionService(db)._exec_end_action(prop)
        await db.commit()

        p_w = (
            await db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_COMMUNITY, Wallet.owner_id == parent,
                )
            )
        ).scalar_one()
        a_w = (await db.execute(select(Wallet).where(Wallet.id == a_w.id))).scalar_one()
        assert p_w.balance == Decimal("25")
        assert a_w.balance == Decimal("0")
