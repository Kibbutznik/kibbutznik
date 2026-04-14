"""Add proposals.prev_content for EditArtifact snapshots

Revision ID: a4b5c6d7e8f9
Revises: 533054e2f87a
Create Date: 2026-04-14 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, None] = '533054e2f87a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('proposals', sa.Column('prev_content', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('proposals', 'prev_content')
