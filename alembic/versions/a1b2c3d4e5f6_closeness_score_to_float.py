"""Closeness score to float

Revision ID: a1b2c3d4e5f6
Revises: 9301186ee1d5
Create Date: 2026-04-08 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9301186ee1d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Scoring formula changed from integer +5/-1 to covariance-per-proposal.
    # Wipe existing data so the new scoring can re-accumulate from scratch.
    op.execute("TRUNCATE TABLE closeness_records")
    op.alter_column(
        'closeness_records',
        'score',
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using='score::double precision',
    )


def downgrade() -> None:
    op.execute("TRUNCATE TABLE closeness_records")
    op.alter_column(
        'closeness_records',
        'score',
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using='score::integer',
    )
