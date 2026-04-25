"""Add communities.charter_md — kibbutz "README" markdown

Communities now carry an optional `charter_md` field: who we are,
how we decide, our norms. Distinct from voted Statements and from
productive Artifacts. Nullable so legacy rows keep working; edits
will move through a ChangeCharter proposal in a follow-up.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-04-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('communities', sa.Column('charter_md', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('communities', 'charter_md')
