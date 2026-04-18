"""Human auth: email + auth_tokens + invites.

Revision ID: e0c1d2e3f4a5
Revises: d7a8f9e0b1c2
Create Date: 2026-04-18 14:45:00.000000

Adds the schema needed to let real humans log in and join communities
alongside the AI agents:

- users.email (nullable, unique when non-NULL): magic-link address.
  Kept nullable because existing agent users don't have one.
- users.is_human: agent vs human flag. Agents stay is_human=False.
- auth_tokens: short-lived magic-link + longer-lived session tokens.
  token_hash is SHA-256 of the raw token so a DB leak doesn't let
  an attacker steal a live session.
- invites: per-community invite codes that create a pending Membership
  proposal when claimed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e0c1d2e3f4a5"
down_revision: Union[str, None] = "d7a8f9e0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- users.email + is_human --------------------------------------
    op.add_column("users", sa.Column("email", sa.String(320), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "is_human",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_users_email_unique",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )

    # ---- auth_tokens --------------------------------------------------
    op.create_table(
        "auth_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "token_hash",
            sa.String(128),
            nullable=False,
            # SHA-256 hex = 64 chars; we keep headroom for the future
        ),
        sa.Column(
            "token_type",
            sa.String(32),
            nullable=False,
            comment="'magic_link' or 'session'",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_auth_tokens_hash",
        "auth_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_auth_tokens_user_type",
        "auth_tokens",
        ["user_id", "token_type"],
    )

    # ---- invites ------------------------------------------------------
    op.create_table(
        "invites",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("community_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "creator_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "invite_code",
            sa.String(64),
            nullable=False,
            comment="URL-safe random code shared in the invite link",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "claimed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_invites_code",
        "invites",
        ["invite_code"],
        unique=True,
    )
    op.create_index(
        "ix_invites_community",
        "invites",
        ["community_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_invites_community", table_name="invites")
    op.drop_index("ix_invites_code", table_name="invites")
    op.drop_table("invites")

    op.drop_index("ix_auth_tokens_user_type", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_hash", table_name="auth_tokens")
    op.drop_table("auth_tokens")

    op.drop_index("ix_users_email_unique", table_name="users")
    op.drop_column("users", "is_human")
    op.drop_column("users", "email")
