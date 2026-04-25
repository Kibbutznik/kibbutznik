"""Partial unique index on users.email — backstop for magic-link race

users.email is nullable (bot/test accounts have no email). Two
concurrent magic-link requests for the same email used to slip
past the SELECT-then-INSERT dedupe in
AuthService.get_or_create_human, leaving two User rows with the
same email and different user_names. The partial UNIQUE index
fires IntegrityError on the loser's INSERT so the service can
re-find and return the winner's row.

If existing data has duplicates, this migration FAILS LOUDLY
rather than picking a winner — an operator must merge the
duplicates by hand (most safely: pick the older user_id, repoint
auth_tokens, members, proposals, etc., then DELETE the newer).
That's a deliberate trade — silent automatic dedupe risks losing
a user's data.

Revision ID: m1n2o3p4q5r6
Revises: b5c6d7e8f9a0
Create Date: 2026-04-25 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'm1n2o3p4q5r6'
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Partial — NULL emails (bot/test users) don't share the index.
    # Lowercase comparison via LOWER() so "Alice@x.com" and
    # "alice@x.com" can't bypass the constraint by varying case.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_lower_unique "
        "ON users (LOWER(email)) WHERE email IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_email_lower_unique")
