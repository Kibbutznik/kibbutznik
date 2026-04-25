"""Add proposals.decided_at — when a proposal flipped to terminal state

Powers the /communities/{id}/audit endpoint without forcing it to
walk pulse history. NULL while DRAFT/OUT_THERE/ON_THE_AIR; set to
NOW() when status flips to ACCEPTED/REJECTED/CANCELED.

Revision ID: g1a2b3c4d5e6
Revises: b5c6d7e8f9a0
Create Date: 2026-04-25 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g1a2b3c4d5e6'
# Re-pointed from the never-merged c6d7e8f9a0b1 (community charter, PR
# #10) to b5c6d7e8f9a0 (add_proposal_pitch — the actual prior head on
# main). The audit-log PR was branched from a charter-feature-aware
# main and slipped through review with the dangling reference.
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'proposals',
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: for rows already in a terminal state, use created_at
    # so they show up in audit ordering rather than sorting last.
    op.execute(
        "UPDATE proposals "
        "SET decided_at = created_at "
        "WHERE proposal_status IN ('Accepted', 'Rejected', 'Canceled')"
    )


def downgrade() -> None:
    op.drop_column('proposals', 'decided_at')
