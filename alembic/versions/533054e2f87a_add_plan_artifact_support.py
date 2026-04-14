"""Add plan artifact support

Revision ID: 533054e2f87a
Revises: 81757c83f1fe
Create Date: 2026-04-14 17:37:03.385987

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '533054e2f87a'
down_revision: Union[str, None] = '81757c83f1fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('artifacts', sa.Column('is_plan', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.alter_column('artifacts', 'proposal_id',
               existing_type=sa.UUID(),
               nullable=True)


def downgrade() -> None:
    op.alter_column('artifacts', 'proposal_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.drop_column('artifacts', 'is_plan')
