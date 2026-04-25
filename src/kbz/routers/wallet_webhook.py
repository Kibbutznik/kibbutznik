"""External-money-in webhook. THE ONLY way credits enter the system
from outside.

Phase 1: HMAC-SHA256 signature against `KBZ_WEBHOOK_SECRET`. Any
external payer that can reach this endpoint and knows the shared
secret can mint credits into a community / action / user wallet.

Phase 2+: The same endpoint shape will be driven by real rails via
adapter processes that translate Stripe / Safe / Open-Collective
events into this canonical shape. The body stays stable across
backings; only the `event` string changes.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.config import settings
from kbz.database import get_db
from kbz.models.wallet import (
    OWNER_ACTION,
    OWNER_COMMUNITY,
    OWNER_USER,
    Wallet,
)
from kbz.services.wallet_backing import resolve_backing
from kbz.services.wallet_service import (
    FinancialModuleDisabledError,
    WalletService,
)

router = APIRouter(tags=["wallet-webhook"])


# The one valid body shape. Phase 2+ real-rail adapters conform to this.
class DepositIn(BaseModel):
    target_kind: str    # "community" | "action" | "user"
    target_id: uuid.UUID
    amount: str         # stringified Decimal
    event: str          # e.g. "stripe.payment_intent.succeeded", "test.seed"
    external_ref: str   # idempotent-reference from the rail (charge id, tx hash)
    idempotency_key: str


@router.post("/webhooks/wallet-deposit")
async def wallet_deposit(
    request: Request,
    x_kbz_signature: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Authenticated external-money-in. HMAC-signed body; idempotent
    on `(event, idempotency_key)`.

    Body — see `DepositIn`. The endpoint does NOT accept JSON through
    FastAPI's auto-parsing because we need the raw bytes for HMAC
    verification before we trust anything.
    """
    if not settings.webhook_secret:
        # The backing's verify_webhook returns False when the secret
        # is unset, but short-circuit here for a clearer error.
        raise HTTPException(
            status_code=503,
            detail="webhook disabled (KBZ_WEBHOOK_SECRET unset on server)",
        )

    raw = await request.body()
    backing = resolve_backing("internal", webhook_secret=settings.webhook_secret)
    if not backing.verify_webhook(
        signature_header=x_kbz_signature, raw_body=raw,
    ):
        raise HTTPException(status_code=401, detail="bad signature")

    # Body is now trusted — parse it.
    try:
        import json
        body_json = json.loads(raw)
        body = DepositIn.model_validate(body_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid body: {e}")

    try:
        amount = Decimal(body.amount)
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="amount must be positive")

    svc = WalletService(db)

    # Dedupe by (event, idempotency_key). If we already processed this
    # exact event, return the previous outcome rather than double-mint.
    existing = await svc.find_webhook(
        event=body.event, idempotency_key=body.idempotency_key,
    )
    if existing is not None:
        return {"status": "duplicate", "ledger_entry_id": str(existing.ledger_entry_id)}

    # Resolve the target wallet. Community/action owners need the
    # financial gate; user owners are platform-wide.
    if body.target_kind == OWNER_COMMUNITY:
        if not await svc.is_financial(body.target_id):
            raise HTTPException(
                status_code=404,
                detail="Target community doesn't have the Financial module enabled.",
            )
        wallet = await svc.get_or_create(OWNER_COMMUNITY, body.target_id)
    elif body.target_kind == OWNER_ACTION:
        # Check parent community's financial state
        from sqlalchemy import select
        from kbz.models.action import Action
        parent = (
            await db.execute(
                select(Action.parent_community_id)
                .where(Action.action_id == body.target_id)
            )
        ).scalar_one_or_none()
        if parent is None or not await svc.is_financial(parent):
            raise HTTPException(
                status_code=404, detail="Action not financial",
            )
        wallet = await svc.get_or_create(OWNER_ACTION, body.target_id)
    elif body.target_kind == OWNER_USER:
        # User wallets always work (welcome credits, dividends, etc.)
        wallet = await svc.get_or_create(OWNER_USER, body.target_id, gate=False)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"unknown target_kind {body.target_kind!r}",
        )

    entry = await svc.mint(
        wallet, amount,
        webhook_event=body.event,
        external_ref=body.external_ref,
        memo=f"deposit via {body.event}",
    )
    # Idempotency dedupe row. The unique index on
    # (event, idempotency_key) enforces single-mint even when two
    # concurrent requests slip past the find_webhook check above
    # — they'll both reach this insert, the second one fails with
    # IntegrityError, we roll back its own mint, and we report
    # the WINNING request's ledger_entry_id back to the loser.
    # Without this race-handling, the prior code would 500 the
    # loser AND leave its mint committed = double-credit.
    try:
        await svc.record_webhook(
            event=body.event,
            idempotency_key=body.idempotency_key,
            ledger_entry_id=entry.id,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await svc.find_webhook(
            event=body.event, idempotency_key=body.idempotency_key,
        )
        if winner is None:
            # Defense-in-depth: the unique-index error fired but
            # we can't find the winning row. Surface a clean 409
            # rather than a confusing 500 — the caller can retry.
            raise HTTPException(
                status_code=409,
                detail="webhook concurrent collision; retry idempotently",
            )
        return {
            "status": "duplicate",
            "ledger_entry_id": str(winner.ledger_entry_id),
        }
    return {
        "status": "credited",
        "ledger_entry_id": str(entry.id),
        "wallet_id": str(wallet.id),
    }
