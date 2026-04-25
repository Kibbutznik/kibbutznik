"""Add notifications table — per-user durable inbox

Solves the "what's new since I last looked" gap. event_bus already
broadcasts to /ws/events subscribers; this gives the same signals
a persistent surface a member sees on next visit.

Revision ID: d8e9f0a1b2c3
Revises: e9f0a1b2c3d4
Create Date: 2026-04-25 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd8e9f0a1b2c3'
# Chain after m1n2o3p4q5r6 (users_email_unique) so the alembic
# graph stays single-headed once flags + amendment chain land.
# Originally pointed at e9f0a1b2c3d4 (reasons) which left
# n2o3p4q5r6s7 → m1n2o3p4q5r6 as a sibling branch. Notifications
# is independent of every migration between e9f0a1b2c3d4 and
# m1n2o3p4q5r6 (separate tables / columns) so the order shift is
# purely structural.
down_revision: Union[str, None] = 'm1n2o3p4q5r6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('community_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('kind', sa.String(64), nullable=False),
        sa.Column('payload_json', postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'idx_notifications_user_unread',
        'notifications',
        ['user_id', 'read_at', 'created_at'],
    )
    op.create_index(
        'idx_notifications_user_community',
        'notifications',
        ['user_id', 'community_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_notifications_user_community', table_name='notifications')
    op.drop_index('idx_notifications_user_unread', table_name='notifications')
    op.drop_table('notifications')
