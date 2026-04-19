"""Bot profiles: let a human delegate their presence in a kibbutz to an
AI proxy configured by a few human-set fields.

Revision ID: f1d2e3f4a5b6
Revises: e0c1d2e3f4a5
Create Date: 2026-04-19 18:00:00.000000

The bot acts AS the user (same user_id on proposals/supports/comments).
No separate bot user — a human can toggle their bot off at any time and
pick up the account manually. One profile per (user, community).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1d2e3f4a5b6"
down_revision: Union[str, None] = "e0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("community_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "display_name",
            sa.String(100),
            nullable=True,
            comment="Optional — bot introduces itself with this name in "
                    "chat/comments. Falls back to '<user_name>-bot'.",
        ),
        sa.Column(
            "orientation",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pragmatist'"),
            comment="producer | consensus | devils_advocate | idealist | pragmatist | diplomat",
        ),
        sa.Column(
            "initiative",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
            comment="1-10: how often the bot proposes vs. observes",
        ),
        sa.Column(
            "agreeableness",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
            comment="1-10: how readily it supports others' proposals",
        ),
        sa.Column(
            "goals",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
            comment="Free text — what the human wants this kibbutz to achieve",
        ),
        sa.Column(
            "boundaries",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
            comment="Free text — what the bot must NOT do",
        ),
        sa.Column(
            "approval_mode",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'autonomous'"),
            comment="autonomous | review (review = draft, require human confirm)",
        ),
        sa.Column(
            "turn_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("300"),
            comment="Min seconds between bot turns. 300 = 5 min.",
        ),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_bot_profiles_user_community",
        "bot_profiles",
        ["user_id", "community_id"],
        unique=True,
    )
    op.create_index(
        "ix_bot_profiles_active",
        "bot_profiles",
        ["active", "last_turn_at"],
        postgresql_where=sa.text("active = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_bot_profiles_active", table_name="bot_profiles")
    op.drop_index("ix_bot_profiles_user_community", table_name="bot_profiles")
    op.drop_table("bot_profiles")
