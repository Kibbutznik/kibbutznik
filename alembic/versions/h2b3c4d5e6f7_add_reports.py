"""Add reports table — moderation flags

Closes the "first harasser arrives uninvited" gap. ThrowOut is
the heavyweight removal path; Report is a lightweight signal a
community can act on without burning a full proposal cycle.

Revision ID: h2b3c4d5e6f7
Revises: e9f0a1b2c3d4
Create Date: 2026-04-25 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'h2b3c4d5e6f7'
down_revision: Union[str, None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reports',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('community_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('reporter_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('target_kind', sa.String(16), nullable=False),
        sa.Column('target_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('reason_text', sa.Text(), nullable=False),
        sa.Column('status', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('resolver_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        'idx_reports_community_status', 'reports', ['community_id', 'status']
    )
    op.create_index('idx_reports_target', 'reports', ['target_kind', 'target_id'])


def downgrade() -> None:
    op.drop_index('idx_reports_target', table_name='reports')
    op.drop_index('idx_reports_community_status', table_name='reports')
    op.drop_table('reports')
