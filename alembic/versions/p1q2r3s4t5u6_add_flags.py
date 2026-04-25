"""Add flags table — symmetric +1/-1 community signal on content

Replaces the abandoned moderation-reports framing (PR #17). Each
row is one flagger's mark on one target (comment / proposal /
reason / user) with value -1 or +1. Re-flagging is REPLACE — the
unique index forces it. Side effect lives in FlagService:
applying or clearing a flag bumps the closeness score between
flagger and the target's author.

Revision ID: p1q2r3s4t5u6
Revises: d8e9f0a1b2c3
Create Date: 2026-04-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'p1q2r3s4t5u6'
# Chain after the notifications head. Note: main currently has TWO
# heads (this one is now downstream of d8e9f0a1b2c3, m1n2o3p4q5r6
# branches off n2o3p4q5r6s7 → e9f0a1b2c3d4 separately). A follow-up
# alembic-chain PR will linearize them; the flags feature itself
# only depends on the table set up to e9f0a1b2c3d4.
down_revision: Union[str, None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'flags',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'flagger_user_id', postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column(
            'community_id', postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column('target_kind', sa.String(16), nullable=False),
        sa.Column('target_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('value', sa.Integer(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            'flagger_user_id', 'target_kind', 'target_id',
            name='uq_flags_flagger_target',
        ),
        sa.CheckConstraint('value IN (-1, 1)', name='ck_flags_value'),
    )
    op.create_index(
        'idx_flags_target', 'flags', ['target_kind', 'target_id'],
    )
    op.create_index(
        'idx_flags_flagger_community', 'flags',
        ['flagger_user_id', 'community_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_flags_flagger_community', table_name='flags')
    op.drop_index('idx_flags_target', table_name='flags')
    op.drop_table('flags')
