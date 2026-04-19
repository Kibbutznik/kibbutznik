"""Financial module: wallets + ledger + webhook idempotency.

Revision ID: b3c4d5e6f7a8
Revises: a7b8c9d0e1f2
Create Date: 2026-04-19 23:00:00.000000

Adds the schema that backs the opt-in finance module. Does NOT alter
`communities` — module enablement lives in the `variables` row
`Financial=<backing>` (default `false`). Wallets are created lazily
on first transaction for opt-in communities.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_kind",
            sa.Text(),
            nullable=False,
        ),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "balance",
            sa.Numeric(18, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "owner_kind IN ('community','action','user','escrow')",
            name="wallets_owner_kind_check",
        ),
        sa.CheckConstraint("balance >= 0", name="wallets_balance_nonneg"),
    )
    op.create_index(
        "ix_wallets_owner_unique",
        "wallets",
        ["owner_kind", "owner_id"],
        unique=True,
    )

    op.create_table(
        "ledger_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("from_wallet", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_wallet", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("round_num", sa.Integer(), nullable=True),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("webhook_event", sa.Text(), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("amount > 0", name="ledger_amount_pos"),
        sa.CheckConstraint(
            "(from_wallet IS NOT NULL) OR (to_wallet IS NOT NULL)",
            name="ledger_at_least_one_side",
        ),
        sa.CheckConstraint(
            "(proposal_id IS NOT NULL) OR (external_ref IS NOT NULL)",
            name="ledger_authz_required",
        ),
    )
    op.create_index(
        "idx_ledger_from_time",
        "ledger_entries",
        ["from_wallet", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_ledger_to_time",
        "ledger_entries",
        ["to_wallet", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_ledger_ext_ref",
        "ledger_entries",
        ["external_ref"],
        postgresql_where=sa.text("external_ref IS NOT NULL"),
    )

    op.create_table(
        "wallet_webhook_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("ledger_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_webhook_events_dedupe",
        "wallet_webhook_events",
        ["event", "idempotency_key"],
        unique=True,
    )

    # Seed `Financial=false` variable for every existing community so
    # the variable-lookup path always finds a row (new communities get
    # it via DEFAULT_VARIABLES in enums.py going forward).
    # The variables table has (community_id, name) as composite PK —
    # no `id` or `created_at` columns.
    op.execute(
        """
        INSERT INTO variables (community_id, name, value)
        SELECT c.id, 'Financial', 'false'
        FROM communities c
        WHERE NOT EXISTS (
            SELECT 1 FROM variables v
            WHERE v.community_id = c.id AND v.name = 'Financial'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM variables WHERE name = 'Financial'")
    op.drop_index("ix_webhook_events_dedupe", table_name="wallet_webhook_events")
    op.drop_table("wallet_webhook_events")
    op.drop_index("idx_ledger_ext_ref", table_name="ledger_entries")
    op.drop_index("idx_ledger_to_time", table_name="ledger_entries")
    op.drop_index("idx_ledger_from_time", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_wallets_owner_unique", table_name="wallets")
    op.drop_table("wallets")
