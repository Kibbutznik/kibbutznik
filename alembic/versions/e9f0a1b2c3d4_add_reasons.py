"""Add reasons table — structured deliberation tree under a proposal

Closes the "argue substantively before voting" gap. Comment is
chit-chat; Reason is a stance-marked claim that members can reply
to with counter-claims, forming a pro/con tree. Hot path: list
all reasons for a proposal — the index covers it.

Revision ID: e9f0a1b2c3d4
Revises: g1a2b3c4d5e6
Create Date: 2026-04-25 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e9f0a1b2c3d4'
# Re-pointed from the never-merged c6d7e8f9a0b1 (charter, PR #10) to
# g1a2b3c4d5e6 (decided_at — also re-pointed in the same fix). Both
# migrations are independent (reasons table vs proposals.decided_at
# column), so chaining them linearly here is safe — order doesn't
# matter operationally, but we need a single head for `alembic
# upgrade head` to work.
down_revision: Union[str, None] = 'g1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reasons',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('proposal_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('stance', sa.String(8), nullable=False),
        sa.Column('claim_text', sa.Text(), nullable=False),
        sa.Column(
            'parent_reason_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('reasons.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('status', sa.Integer(), nullable=False, server_default='1'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index('idx_reasons_proposal', 'reasons', ['proposal_id'])
    op.create_index('idx_reasons_parent', 'reasons', ['parent_reason_id'])


def downgrade() -> None:
    op.drop_index('idx_reasons_parent', table_name='reasons')
    op.drop_index('idx_reasons_proposal', table_name='reasons')
    op.drop_table('reasons')
