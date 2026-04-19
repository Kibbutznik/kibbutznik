"""Wallet, LedgerEntry, WalletWebhookEvent — the finance module's
source of truth.

Invariants enforced at the schema level (see
alembic/versions/b3c4d5e6f7a8_financial_module.py):
  - wallet.balance >= 0 (no overdraft, crypto-forward)
  - ledger amount > 0
  - ledger has at least one of (from_wallet, to_wallet) set
  - every ledger entry is authorized by EITHER a proposal_id (pulse
    accepted it) OR an external_ref + webhook_event (signed webhook)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class Wallet(Base):
    __tablename__ = "wallets"
    # Keep the constraints aligned with the Alembic migration so
    # `Base.metadata.create_all` (used by tests) enforces the same
    # invariants the live DB does.
    __table_args__ = (
        CheckConstraint(
            "owner_kind IN ('community','action','user','escrow')",
            name="wallets_owner_kind_check",
        ),
        CheckConstraint("balance >= 0", name="wallets_balance_nonneg"),
        Index("ix_wallets_owner_unique", "owner_kind", "owner_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_kind: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        server_default=text("0"),
        default=Decimal("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ledger_amount_pos"),
        CheckConstraint(
            "(from_wallet IS NOT NULL) OR (to_wallet IS NOT NULL)",
            name="ledger_at_least_one_side",
        ),
        CheckConstraint(
            "(proposal_id IS NOT NULL) OR (external_ref IS NOT NULL)",
            name="ledger_authz_required",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    from_wallet: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    to_wallet: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    round_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_event: Mapped[str | None] = mapped_column(Text, nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class WalletWebhookEvent(Base):
    __tablename__ = "wallet_webhook_events"
    __table_args__ = (
        Index(
            "ix_webhook_events_dedupe",
            "event", "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    event: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    ledger_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


# String constants — avoid magic values in the rest of the code
OWNER_COMMUNITY = "community"
OWNER_ACTION = "action"
OWNER_USER = "user"
OWNER_ESCROW = "escrow"

FINANCIAL_OFF_VALUES = frozenset({"", "false", "False", "FALSE", "off", "0"})
