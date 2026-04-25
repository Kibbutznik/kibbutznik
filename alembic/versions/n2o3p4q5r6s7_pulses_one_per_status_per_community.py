"""Partial unique indexes — at most one NEXT/ACTIVE pulse per community

Two concurrent threshold-crossing pulse-supports both refresh,
both see ``support_count >= threshold``, and both call
``execute_pulse``. Without protection both create a new NEXT
pulse — leaving the community with two NEXT pulses, which breaks
every subsequent ``get_next_pulse()`` with MultipleResultsFound.

The partial unique indexes turn the loser's INSERT into a clean
IntegrityError that the service catches and bails on. Mirrored
in the SQLAlchemy model so test fixtures (which use create_all,
not migrations) enforce the same rule.

If existing prod data has duplicate NEXT or ACTIVE pulses for
any community, this migration FAILS LOUDLY rather than picking a
winner — operator must manually resolve (typically: keep the
oldest, mark the others DONE).

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-04-26 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'n2o3p4q5r6s7'
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_pulses_one_next_per_community "
        "ON pulses (community_id) WHERE status = 0"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_pulses_one_active_per_community "
        "ON pulses (community_id) WHERE status = 1"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pulses_one_active_per_community")
    op.execute("DROP INDEX IF EXISTS ix_pulses_one_next_per_community")
