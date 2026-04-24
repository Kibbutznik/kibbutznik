"""Wallet + ledger endpoints.

Public shape:

    GET  /communities/{id}/wallet           → 404 if not financial
    GET  /actions/{id}/wallet               → 404 if owning community not financial
    GET  /users/me/wallet                   → always works (user wallets are
                                              platform-wide, not community-scoped)
    GET  /communities/{id}/ledger           → paginated entries; 404 if not financial
    POST /actions/{id}/funding-request      → file a Funding proposal in the
                                              parent community
    POST /actions/{id}/payment-request      → file a Payment proposal; leaf-only

Deposits are NOT a proposal — see wallet_webhook.py for external-money-in.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import get_current_user, require_user
from kbz.database import get_db
from kbz.enums import ProposalStatus, ProposalType
from kbz.models.action import Action
from kbz.models.proposal import Proposal
from kbz.models.user import User
from kbz.models.wallet import (
    OWNER_ACTION,
    OWNER_COMMUNITY,
    OWNER_USER,
    LedgerEntry,
    Wallet,
)
from kbz.services.wallet_service import (
    FinancialModuleDisabledError,
    WalletService,
)

router = APIRouter(tags=["wallets"])


# ── Schemas ─────────────────────────────────────────────────────────

class LedgerEntryOut(BaseModel):
    id: uuid.UUID
    from_wallet: uuid.UUID | None
    to_wallet: uuid.UUID | None
    amount: str  # stringified Decimal to avoid float roundtrip
    proposal_id: uuid.UUID | None
    round_num: int | None
    external_ref: str | None
    webhook_event: str | None
    memo: str | None
    created_at: datetime


class WalletOut(BaseModel):
    id: uuid.UUID
    owner_kind: str
    owner_id: uuid.UUID
    balance: str
    recent_entries: list[LedgerEntryOut]


def _wallet_to_out(wallet: Wallet, entries: list[LedgerEntry]) -> WalletOut:
    return WalletOut(
        id=wallet.id,
        owner_kind=wallet.owner_kind,
        owner_id=wallet.owner_id,
        balance=str(wallet.balance),
        recent_entries=[
            LedgerEntryOut(
                id=e.id,
                from_wallet=e.from_wallet,
                to_wallet=e.to_wallet,
                amount=str(e.amount),
                proposal_id=e.proposal_id,
                round_num=e.round_num,
                external_ref=e.external_ref,
                webhook_event=e.webhook_event,
                memo=e.memo,
                created_at=e.created_at,
            )
            for e in entries
        ],
    )


# ── Read endpoints ──────────────────────────────────────────────────

@router.get("/communities/{community_id}/wallet", response_model=WalletOut)
async def get_community_wallet(
    community_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    svc = WalletService(db)
    if not await svc.is_financial(community_id):
        raise HTTPException(
            status_code=404,
            detail="This community doesn't have the Financial module enabled.",
        )
    wallet = await svc.get_or_create(OWNER_COMMUNITY, community_id)
    entries = await svc.recent_entries(wallet.id)
    return _wallet_to_out(wallet, entries)


@router.get("/actions/{action_id}/wallet", response_model=WalletOut)
async def get_action_wallet(
    action_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    # Resolve parent community to check financial status
    parent = (
        await db.execute(
            select(Action.parent_community_id).where(Action.action_id == action_id)
        )
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=404, detail="Action not found")
    svc = WalletService(db)
    if not await svc.is_financial(parent):
        raise HTTPException(
            status_code=404,
            detail="This action's community doesn't have the Financial module enabled.",
        )
    wallet = await svc.get_or_create(OWNER_ACTION, action_id)
    entries = await svc.recent_entries(wallet.id)
    return _wallet_to_out(wallet, entries)


@router.get("/users/me/wallet", response_model=WalletOut)
async def get_my_wallet(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    svc = WalletService(db)
    wallet = await svc.get_or_create(OWNER_USER, user.id, gate=False)
    entries = await svc.recent_entries(wallet.id)
    return _wallet_to_out(wallet, entries)


@router.get(
    "/communities/{community_id}/ledger",
    response_model=list[LedgerEntryOut],
)
async def get_community_ledger(
    community_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    svc = WalletService(db)
    if not await svc.is_financial(community_id):
        raise HTTPException(status_code=404, detail="Not financial")
    wallet = await svc.get_or_create(OWNER_COMMUNITY, community_id)
    rows = (
        await db.execute(
            select(LedgerEntry)
            .where(
                (LedgerEntry.from_wallet == wallet.id)
                | (LedgerEntry.to_wallet == wallet.id)
            )
            .order_by(LedgerEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return [
        LedgerEntryOut(
            id=e.id,
            from_wallet=e.from_wallet,
            to_wallet=e.to_wallet,
            amount=str(e.amount),
            proposal_id=e.proposal_id,
            round_num=e.round_num,
            external_ref=e.external_ref,
            webhook_event=e.webhook_event,
            memo=e.memo,
            created_at=e.created_at,
        )
        for e in rows
    ]


# ── Write endpoints — shortcut composers for Funding / Payment ──

class FundingRequestIn(BaseModel):
    amount: str   # stringified Decimal
    pitch: str = ""


@router.post("/actions/{action_id}/funding-request")
async def file_funding_request(
    action_id: uuid.UUID,
    body: FundingRequestIn,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """File a Funding proposal in the action's PARENT community,
    requesting `amount` credits to transfer into this action's wallet.

    Thin shortcut over POST /communities/{parent_id}/proposals so the
    frontend doesn't have to manually compute the parent id. Handler-
    side execution enforces the financial gate + balance.
    """
    parent = (
        await db.execute(
            select(Action.parent_community_id).where(Action.action_id == action_id)
        )
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=404, detail="Action not found")
    svc = WalletService(db)
    if not await svc.is_financial(parent):
        raise HTTPException(
            status_code=409,
            detail="Parent community doesn't have the Financial module enabled.",
        )
    from kbz.services.member_service import MemberService
    if not await MemberService(db).is_active_member(parent, user.id):
        raise HTTPException(status_code=403, detail="User is not an active member")
    # Validate the amount format upfront — cleaner error than
    # letting it fail at execution time.
    try:
        Decimal(body.amount)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid amount: {body.amount!r}")
    p = Proposal(
        id=uuid.uuid4(),
        community_id=parent,
        user_id=user.id,
        proposal_type=ProposalType.FUNDING,
        proposal_status=ProposalStatus.OUT_THERE,
        proposal_text=body.pitch,
        val_uuid=action_id,
        val_text=body.amount,
        age=0,
        support_count=0,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return {"proposal_id": str(p.id)}


class PaymentRequestIn(BaseModel):
    amount: str
    pitch: str = ""


@router.post("/communities/{community_id}/payment-request")
async def file_payment_request(
    community_id: uuid.UUID,
    body: PaymentRequestIn,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """File a Payment proposal from this community / leaf action. The
    executor enforces the leaf-only constraint (no active children)
    and the financial-module gate."""
    svc = WalletService(db)
    if not await svc.is_financial(community_id):
        raise HTTPException(
            status_code=409,
            detail="Community doesn't have the Financial module enabled.",
        )
    from kbz.services.member_service import MemberService
    if not await MemberService(db).is_active_member(community_id, user.id):
        raise HTTPException(status_code=403, detail="User is not an active member")
    # Early check that this is a leaf — saves a pointless proposal.
    has_children = (
        await db.execute(
            select(Action).where(Action.parent_community_id == community_id)
        )
    ).first() is not None
    if has_children:
        raise HTTPException(
            status_code=409,
            detail="This community has sub-actions. Only leaf communities may file Payment.",
        )
    try:
        Decimal(body.amount)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid amount: {body.amount!r}")
    p = Proposal(
        id=uuid.uuid4(),
        community_id=community_id,
        user_id=user.id,
        proposal_type=ProposalType.PAYMENT,
        proposal_status=ProposalStatus.OUT_THERE,
        proposal_text=body.pitch,
        val_text=body.amount,
        age=0,
        support_count=0,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return {"proposal_id": str(p.id)}
