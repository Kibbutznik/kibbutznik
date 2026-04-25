"""Add comment_votes table — one vote per user per comment

Replaces the unbounded "every POST /score adds delta" behavior with
a real per-user vote table. Reset existing comments.score to 0 in
the same transaction so we start clean — old scores reflect spam-
clicks, not real distinct-user opinions.

Revision ID: q2r3s4t5u6v7
Revises: f0a1b2c3d4e5
Create Date: 2026-04-25 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'q2r3s4t5u6v7'
down_revision: Union[str, None] = 'f0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'comment_votes',
        sa.Column(
            'user_id', postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column(
            'comment_id', postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column('value', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text('NOW()'),
        ),
        sa.PrimaryKeyConstraint(
            'user_id', 'comment_id', name='pk_comment_votes',
        ),
        sa.ForeignKeyConstraint(
            ['comment_id'], ['comments.id'],
            ondelete='CASCADE',
        ),
        sa.CheckConstraint('value IN (-1, 1)', name='ck_comment_votes_value'),
    )
    # Reset cached scores. Pre-fix the score field reflected
    # spam-click totals, not distinct-user signal. Going forward
    # the score is always sum(comment_votes.value) for the comment;
    # zeroing here keeps the cached column honest from day one.
    op.execute("UPDATE comments SET score = 0")


def downgrade() -> None:
    op.drop_table('comment_votes')
    # Don't restore old scores — they were corrupted by the un-deduped
    # path and there's no way to reconstruct them.
