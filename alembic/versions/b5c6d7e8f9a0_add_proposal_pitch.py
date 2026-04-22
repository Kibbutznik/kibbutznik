"""Add proposals.pitch — proposer's "why" rationale text

A Proposal's `proposal_text` holds the *what* (rule text, variable name,
artifact content, etc.). `pitch` is a separate free-text field for the
*why*: the proposer's case for accepting it. Kept nullable so legacy rows
stay valid; new proposals are expected to include one.

Revision ID: b5c6d7e8f9a0
Revises: b3c4d5e6f7a8
Create Date: 2026-04-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('proposals', sa.Column('pitch', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('proposals', 'pitch')
