"""API tokens: let users mint long-lived bearer tokens for external bots.

Revision ID: a7b8c9d0e1f2
Revises: f1d2e3f4a5b6
Create Date: 2026-04-19 18:45:00.000000

Adds a `name` label to auth_tokens so users can distinguish multiple
tokens ("my claude skill", "langchain experiment", …). No new table —
api_token is just another `token_type` in the same row shape, but with
a human-assigned label and a much longer expiry.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "auth_tokens",
        sa.Column(
            "name",
            sa.String(80),
            nullable=True,
            comment="Human label for api_token rows; NULL for magic_link/session",
        ),
    )


def downgrade() -> None:
    op.drop_column("auth_tokens", "name")
