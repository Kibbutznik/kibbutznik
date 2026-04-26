"""WalletService — every ledger mutation goes through here.

Contract:
  - `is_financial(community_id)` is the gate — any write path checks
    it before touching wallets.
  - `get_or_create(owner_kind, owner_id)` lazily materializes a
    wallet row. For communities/actions it refuses if the owning
    community isn't financial.
  - `mint` / `burn` / `transfer` / `escrow_*` are the primitives;
    everything else composes them.
  - Every write goes through `SELECT … FOR UPDATE` on the source
    wallet (`transfer` / `burn` / `escrow_open`) or the target
    (for reconciliation reasons we keep mints sequential too).
  - `wallets.balance` is the denormalized sum of ledger entries; a
    periodic reconciler (`scripts/reconcile_wallets.py`, §verification
    in the plan file) asserts the equality.

Phase-1 scope: InternalBacking (credits only, no external rails).
Phase 2+ plugs Safe / Stripe via `WalletBacking` behind the same
interface. See `CRYPTO_ROADMAP.md` for the on-chain path.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy import and_, select, update
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.models.action import Action
from kbz.models.variable import Variable
from kbz.models.wallet import (
    FINANCIAL_OFF_VALUES,
    OWNER_ACTION,
    OWNER_COMMUNITY,
    OWNER_ESCROW,
    OWNER_USER,
    LedgerEntry,
    Wallet,
    WalletWebhookEvent,
)


# ─── Exceptions ────────────────────────────────────────────────────


class WalletError(Exception):
    """Base class — callers can `except WalletError` to catch all of
    these without importing each subclass."""


class FinancialModuleDisabledError(WalletError):
    """The community doesn't have the Financial module enabled."""


class InsufficientFundsError(WalletError):
    """Source wallet's balance is less than the requested amount."""


class DuplicateWebhookError(WalletError):
    """A webhook event with the same idempotency_key has already been
    processed — caller should return the existing result, not re-run."""


# ─── Helpers ───────────────────────────────────────────────────────


def _parse_amount(raw) -> Decimal:
    """Accept str / int / float / Decimal. Reject non-positive, NaN,
    inf. Quantize to 6 decimals (matches USDC / schema precision)."""
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as e:
        raise ValueError(f"invalid amount: {raw!r}") from e
    if not d.is_finite() or d <= 0:
        raise ValueError(f"amount must be positive finite, got {raw!r}")
    # Quantize down to 6 decimals
    return d.quantize(Decimal("0.000001"))


# ─── Service ───────────────────────────────────────────────────────


class WalletService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---- Module toggle ------------------------------------------------

    async def is_financial(self, community_id: uuid.UUID) -> bool:
        """Check whether the community has the Financial module on.

        Reads from the existing `variables` table (no schema change on
        `communities`). A community is financial when its `Financial`
        variable is set to anything not in FINANCIAL_OFF_VALUES.
        """
        row = (
            await self.db.execute(
                select(Variable.value).where(
                    Variable.community_id == community_id,
                    Variable.name == "Financial",
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        return (row or "").strip() not in FINANCIAL_OFF_VALUES

    async def backing_name(self, community_id: uuid.UUID) -> str:
        """Return the raw variable value ("internal", "safe:0x…", …)
        so the WalletBacking factory can dispatch. "" when off."""
        row = (
            await self.db.execute(
                select(Variable.value).where(
                    Variable.community_id == community_id,
                    Variable.name == "Financial",
                )
            )
        ).scalar_one_or_none()
        value = (row or "").strip()
        return "" if value in FINANCIAL_OFF_VALUES else value

    # ---- Wallet resolution -------------------------------------------

    async def _community_id_for_owner(
        self, owner_kind: str, owner_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Which community is this wallet 'inside'? Used for the
        is_financial gate. `user` and `escrow` wallets are NOT scoped
        to a community — they live across the whole platform."""
        if owner_kind == OWNER_COMMUNITY:
            return owner_id
        if owner_kind == OWNER_ACTION:
            # actions store their parent community under `parent_community_id`
            row = (
                await self.db.execute(
                    select(Action.parent_community_id)
                    .where(Action.action_id == owner_id)
                )
            ).scalar_one_or_none()
            return row
        return None  # user / escrow

    async def get_or_create(
        self, owner_kind: str, owner_id: uuid.UUID, *, gate: bool = True
    ) -> Wallet:
        """Lazily materialize a wallet. Refuses for community/action
        wallets when the owning community is not financial (unless
        `gate=False` — used internally when we KNOW the caller has
        already checked)."""
        from sqlalchemy.exc import IntegrityError as _IntegrityError

        existing = (
            await self.db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == owner_kind,
                    Wallet.owner_id == owner_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        if gate and owner_kind in (OWNER_COMMUNITY, OWNER_ACTION):
            community_id = await self._community_id_for_owner(owner_kind, owner_id)
            if community_id is None or not await self.is_financial(community_id):
                raise FinancialModuleDisabledError(
                    f"cannot create wallet for {owner_kind}:{owner_id} — "
                    "owning community has Financial=false"
                )
        w = Wallet(
            id=uuid.uuid4(),
            owner_kind=owner_kind,
            owner_id=owner_id,
            balance=Decimal("0"),
        )
        self.db.add(w)
        # Race-window safety: two concurrent first-access requests for
        # the same wallet both pass the SELECT above before either
        # flushes. The unique index ix_wallets_owner_unique on
        # (owner_kind, owner_id) catches the loser's INSERT — without
        # this guard the loser sees a 500 instead of the winning
        # wallet. Symmetric to the proposal-support / magic-link
        # race fixes (PR #20, PR #21).
        try:
            await self.db.flush()
        except _IntegrityError:
            await self.db.rollback()
            winner = (
                await self.db.execute(
                    select(Wallet).where(
                        Wallet.owner_kind == owner_kind,
                        Wallet.owner_id == owner_id,
                    )
                )
            ).scalar_one_or_none()
            if winner is None:
                # Defense-in-depth: index fired but we can't find the
                # winner. Re-raise so we don't return None silently.
                raise
            return winner
        return w

    async def balance(self, wallet_id: uuid.UUID) -> Decimal:
        row = (
            await self.db.execute(
                select(Wallet.balance).where(Wallet.id == wallet_id)
            )
        ).scalar_one()
        return row

    # ---- Primitives --------------------------------------------------

    async def mint(
        self,
        to_wallet: Wallet,
        amount,
        *,
        webhook_event: str,
        external_ref: str | None,
        memo: str | None = None,
        round_num: int | None = None,
    ) -> LedgerEntry:
        """Credits enter the system. Only called by the webhook route
        (Phase 1) or the welcome-credits provisioner. Needs either
        `external_ref` or `webhook_event` set (schema CHECK).
        """
        amt = _parse_amount(amount)
        # Lock the target row during update so two concurrent mints
        # serialize (rare but possible with parallel webhooks).
        await self.db.execute(
            select(Wallet.id).where(Wallet.id == to_wallet.id).with_for_update()
        )
        entry = LedgerEntry(
            id=uuid.uuid4(),
            from_wallet=None,
            to_wallet=to_wallet.id,
            amount=amt,
            proposal_id=None,
            round_num=round_num,
            external_ref=external_ref or webhook_event,  # at least one
            webhook_event=webhook_event,
            memo=memo,
        )
        self.db.add(entry)
        await self.db.execute(
            update(Wallet)
            .where(Wallet.id == to_wallet.id)
            .values(balance=Wallet.balance + amt)
        )
        await self.db.flush()
        return entry

    async def burn(
        self,
        from_wallet: Wallet,
        amount,
        *,
        proposal_id: uuid.UUID,
        memo: str | None = None,
        round_num: int | None = None,
    ) -> LedgerEntry:
        """Credits leave the system. Authorized only by an accepted
        proposal — schema CHECK enforces that."""
        amt = _parse_amount(amount)
        # SELECT FOR UPDATE to prevent double-spend
        row = (
            await self.db.execute(
                select(Wallet).where(Wallet.id == from_wallet.id).with_for_update()
            )
        ).scalar_one()
        if row.balance < amt:
            raise InsufficientFundsError(
                f"wallet {from_wallet.id} has {row.balance}, need {amt}"
            )
        entry = LedgerEntry(
            id=uuid.uuid4(),
            from_wallet=from_wallet.id,
            to_wallet=None,
            amount=amt,
            proposal_id=proposal_id,
            round_num=round_num,
            external_ref=None,
            webhook_event=None,
            memo=memo,
        )
        self.db.add(entry)
        await self.db.execute(
            update(Wallet)
            .where(Wallet.id == from_wallet.id)
            .values(balance=Wallet.balance - amt)
        )
        await self.db.flush()
        return entry

    async def transfer(
        self,
        from_wallet: Wallet,
        to_wallet: Wallet,
        amount,
        *,
        proposal_id: uuid.UUID | None = None,
        memo: str | None = None,
        round_num: int | None = None,
    ) -> LedgerEntry:
        """Wallet → wallet transfer, atomic. Needs a `proposal_id`
        OR an `external_ref` (passed via the webhook path) — here
        we default to proposal_id, callers should supply one. The
        escrow helpers below pass `proposal_id` to satisfy the
        schema authz CHECK.
        """
        amt = _parse_amount(amount)
        if from_wallet.id == to_wallet.id:
            raise ValueError("cannot transfer to the same wallet")
        # Deterministic lock order to avoid deadlocks
        ids = sorted([from_wallet.id, to_wallet.id])
        for wid in ids:
            await self.db.execute(
                select(Wallet.id).where(Wallet.id == wid).with_for_update()
            )
        src = (
            await self.db.execute(select(Wallet).where(Wallet.id == from_wallet.id))
        ).scalar_one()
        if src.balance < amt:
            raise InsufficientFundsError(
                f"wallet {from_wallet.id} has {src.balance}, need {amt}"
            )
        if proposal_id is None:
            # Authz CHECK requires proposal_id OR external_ref; callers
            # that don't have either should use mint/burn instead.
            raise ValueError(
                "transfer requires proposal_id (or use mint/burn for "
                "webhook-authorized moves)"
            )
        entry = LedgerEntry(
            id=uuid.uuid4(),
            from_wallet=from_wallet.id,
            to_wallet=to_wallet.id,
            amount=amt,
            proposal_id=proposal_id,
            round_num=round_num,
            external_ref=None,
            webhook_event=None,
            memo=memo,
        )
        self.db.add(entry)
        await self.db.execute(
            update(Wallet)
            .where(Wallet.id == from_wallet.id)
            .values(balance=Wallet.balance - amt)
        )
        await self.db.execute(
            update(Wallet)
            .where(Wallet.id == to_wallet.id)
            .values(balance=Wallet.balance + amt)
        )
        await self.db.flush()
        return entry

    # ---- Sweep on action close ---------------------------------------

    async def sweep_action_to_parent(
        self,
        action_id: uuid.UUID,
        *,
        proposal_id: uuid.UUID,
    ) -> LedgerEntry | None:
        """When an EndAction is accepted, move the action's full
        balance to its parent community's wallet. Returns the
        ledger entry (or None if no balance to move)."""
        src_wallet = (
            await self.db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_ACTION,
                    Wallet.owner_id == action_id,
                )
            )
        ).scalar_one_or_none()
        if src_wallet is None or src_wallet.balance <= 0:
            return None
        # Find the parent community
        community_id = await self._community_id_for_owner(OWNER_ACTION, action_id)
        if community_id is None:
            return None
        parent_wallet = await self.get_or_create(
            OWNER_COMMUNITY, community_id, gate=False
        )
        return await self.transfer(
            src_wallet, parent_wallet, src_wallet.balance,
            proposal_id=proposal_id, memo="action close sweep",
        )

    # ---- Escrow ------------------------------------------------------

    async def escrow_open(
        self,
        proposal_id: uuid.UUID,
        amount,
        from_wallet: Wallet,
        memo: str | None = None,
    ) -> Wallet:
        """Debit `amount` from `from_wallet`, park it in a new escrow
        wallet keyed on `proposal_id`. Caller must know which wallet
        to unwind to — we store the originating user_id via memo on
        the inbound ledger entry so refund can look it up."""
        escrow = Wallet(
            id=uuid.uuid4(),
            owner_kind=OWNER_ESCROW,
            owner_id=proposal_id,
            balance=Decimal("0"),
        )
        self.db.add(escrow)
        await self.db.flush()
        # Remember the source wallet id in the memo of the opening
        # transfer so refund can find it without a separate column.
        source_memo = f"escrow:src={from_wallet.id}"
        await self.transfer(
            from_wallet, escrow, amount,
            proposal_id=proposal_id, memo=source_memo,
        )
        return escrow

    async def _escrow_for_proposal(
        self, proposal_id: uuid.UUID
    ) -> Wallet | None:
        return (
            await self.db.execute(
                select(Wallet).where(
                    Wallet.owner_kind == OWNER_ESCROW,
                    Wallet.owner_id == proposal_id,
                )
            )
        ).scalar_one_or_none()

    async def _escrow_source(self, escrow_id: uuid.UUID) -> uuid.UUID | None:
        """Recover the original source wallet id from the memo of the
        escrow-opening transfer (only inbound entry on an escrow)."""
        row = (
            await self.db.execute(
                select(LedgerEntry.memo).where(
                    LedgerEntry.to_wallet == escrow_id,
                )
                .order_by(LedgerEntry.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None or not row.startswith("escrow:src="):
            return None
        try:
            return uuid.UUID(row.split("=", 1)[1])
        except (ValueError, IndexError):
            return None

    async def escrow_release(
        self,
        proposal_id: uuid.UUID,
        to_wallet: Wallet,
    ) -> LedgerEntry | None:
        """Membership accepted → escrow flows to the community wallet."""
        escrow = await self._escrow_for_proposal(proposal_id)
        if escrow is None or escrow.balance <= 0:
            return None
        entry = await self.transfer(
            escrow, to_wallet, escrow.balance,
            proposal_id=proposal_id, memo="escrow release",
        )
        # Escrow is now empty — delete the ephemeral row
        await self.db.delete(escrow)
        await self.db.flush()
        return entry

    async def escrow_refund(
        self, proposal_id: uuid.UUID
    ) -> LedgerEntry | None:
        """Membership rejected / canceled → escrow goes back to the
        original applicant's user wallet."""
        escrow = await self._escrow_for_proposal(proposal_id)
        if escrow is None or escrow.balance <= 0:
            return None
        src_id = await self._escrow_source(escrow.id)
        if src_id is None:
            # Shouldn't happen, but defend — drop the funds on the
            # floor rather than leak them into nowhere.
            return None
        src = (
            await self.db.execute(select(Wallet).where(Wallet.id == src_id))
        ).scalar_one()
        entry = await self.transfer(
            escrow, src, escrow.balance,
            proposal_id=proposal_id, memo="escrow refund",
        )
        await self.db.delete(escrow)
        await self.db.flush()
        return entry

    # ---- Webhook idempotency ----------------------------------------

    async def record_webhook(
        self,
        *,
        event: str,
        idempotency_key: str,
        ledger_entry_id: uuid.UUID,
    ) -> WalletWebhookEvent:
        """Insert the dedupe row. Caller should catch IntegrityError
        and treat as DuplicateWebhookError."""
        row = WalletWebhookEvent(
            id=uuid.uuid4(),
            event=event,
            idempotency_key=idempotency_key,
            ledger_entry_id=ledger_entry_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def find_webhook(
        self, *, event: str, idempotency_key: str
    ) -> WalletWebhookEvent | None:
        return (
            await self.db.execute(
                select(WalletWebhookEvent).where(
                    WalletWebhookEvent.event == event,
                    WalletWebhookEvent.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()

    # ---- Read helpers used by routers -------------------------------

    async def recent_entries(
        self, wallet_id: uuid.UUID, *, limit: int = 20
    ) -> list[LedgerEntry]:
        rows = (
            await self.db.execute(
                select(LedgerEntry)
                .where(
                    (LedgerEntry.from_wallet == wallet_id)
                    | (LedgerEntry.to_wallet == wallet_id)
                )
                .order_by(LedgerEntry.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return list(rows)
