"""Add contact_messages table — inbound "Get in touch" messages

Persists public contact-form submissions so a message is never lost to
an email outage; the operator reads them via GET /admin/contact (and,
when KBZ_CONTACT_NOTIFY_EMAIL is set, a best-effort email).

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
Create Date: 2026-05-23 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'r3s4t5u6v7w8'
down_revision: Union[str, None] = 'q2r3s4t5u6v7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'contact_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(120), nullable=True),
        sa.Column('email', sa.String(320), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('ip', sa.String(64), nullable=True),
        sa.Column('user_agent', sa.String(512), nullable=True),
        sa.Column('handled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('idx_contact_messages_created', 'contact_messages', ['created_at'])
    op.create_index('idx_contact_messages_handled_created', 'contact_messages', ['handled', 'created_at'])


def downgrade() -> None:
    op.drop_index('idx_contact_messages_handled_created', table_name='contact_messages')
    op.drop_index('idx_contact_messages_created', table_name='contact_messages')
    op.drop_table('contact_messages')
