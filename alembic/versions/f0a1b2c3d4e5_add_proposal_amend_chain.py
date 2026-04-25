"""Add proposals.parent_proposal_id + version — amendment chain

Closes the "first contested proposal will need a clarifying tweak
mid-pulse" gap. Today, the only way to change a proposal's text
mid-flight is /edit which destroys all existing support and stays
on the same row — there's no record of the prior version. Amend
preserves history: the predecessor row stays at status CANCELED
with its prev text intact, and the successor carries
parent_proposal_id back at it.

Revision ID: f0a1b2c3d4e5
Revises: m1n2o3p4q5r6
Create Date: 2026-04-25 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f0a1b2c3d4e5'
# Chain after the flags head (p1q2r3s4t5u6) so alembic has a single
# head. Originally pointed at m1n2o3p4q5r6 which created a sibling
# branch alongside the d8e9f0a1b2c3 → p1q2r3s4t5u6 chain. Both
# migrations are independent (this adds Proposal columns; flags
# adds a separate table) so chain order doesn't matter
# operationally — we just need a single head for `alembic upgrade
# head` to work.
down_revision: Union[str, None] = 'p1q2r3s4t5u6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'proposals',
        sa.Column('parent_proposal_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        'proposals',
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
    )
    op.create_index(
        'idx_proposals_parent', 'proposals', ['parent_proposal_id']
    )


def downgrade() -> None:
    op.drop_index('idx_proposals_parent', table_name='proposals')
    op.drop_column('proposals', 'version')
    op.drop_column('proposals', 'parent_proposal_id')
