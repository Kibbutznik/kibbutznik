"""Magic-link auth for human users.

Flow:
    1. POST /auth/request-magic-link {email}
       → If no user with that email exists, create one (user_name = email local
         part + short suffix, is_human=True). Generate a magic-link token,
         store SHA-256 of it, return the verify URL in dev mode (or email it
         in prod — not implemented yet, see `auth_dev_expose_magic_link`).
    2. GET /auth/verify?token=<raw>
       → Look up by SHA-256(token). If valid + not expired + not used,
         mark used_at=NOW, create a session token, set an httponly cookie,
         redirect to the viewer.
    3. Subsequent requests carry the cookie; `get_current_user` resolves
       it back to a User.
    4. POST /auth/logout → clears the cookie and invalidates the session token.

Security notes:
    - Raw tokens are NEVER stored — only SHA-256(token) lives in the DB.
      A read-only DB leak therefore does NOT let an attacker forge a
      live session: they'd need the raw token which only exists on the
      wire once.
    - Magic-link tokens are single-use (`used_at` is set on first verify).
    - Session tokens don't have `used_at` — they're verified by
      (hash match + not expired).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.config import settings
from kbz.models.auth import AuthToken
from kbz.models.user import User


TOKEN_TYPE_MAGIC = "magic_link"
TOKEN_TYPE_SESSION = "session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _random_token(nbytes: int = 32) -> str:
    """URL-safe random token, ~43 chars at nbytes=32."""
    return secrets.token_urlsafe(nbytes)


@dataclass
class IssuedToken:
    """The raw token PLUS the DB row. The raw is only returned once."""
    raw: str
    token_id: uuid.UUID
    expires_at: datetime


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---- user lookup / creation -------------------------------------

    async def find_user_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        if not normalized:
            return None
        row = (
            await self.db.execute(select(User).where(User.email == normalized))
        ).scalar_one_or_none()
        return row

    async def get_or_create_human(self, email: str) -> User:
        """Idempotent: returns existing user for email, or creates one.

        The new user gets a unique `user_name` derived from the email's
        local part so the existing username-based viewer UI still works.
        We append a short random suffix to avoid collisions.
        """
        normalized = email.strip().lower()
        if not normalized or "@" not in normalized:
            raise ValueError("invalid email")
        existing = await self.find_user_by_email(normalized)
        if existing:
            return existing
        local = normalized.split("@", 1)[0]
        # Strip non-alnum and cap at 24 chars for a tidy user_name
        local_clean = "".join(c for c in local if c.isalnum()) or "user"
        suffix = secrets.token_hex(3)  # 6 hex chars
        user_name = f"{local_clean[:24]}_{suffix}"
        user = User(
            id=uuid.uuid4(),
            user_name=user_name,
            password_hash="",  # unused for magic-link users
            about="",
            wallet_address="",
            email=normalized,
            is_human=True,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    # ---- token issuance / verification ------------------------------

    async def issue_magic_link(self, user: User) -> IssuedToken:
        raw = _random_token()
        expires = _now() + timedelta(minutes=settings.auth_magic_link_ttl_minutes)
        token = AuthToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=_hash_token(raw),
            token_type=TOKEN_TYPE_MAGIC,
            expires_at=expires,
        )
        self.db.add(token)
        await self.db.flush()
        return IssuedToken(raw=raw, token_id=token.id, expires_at=expires)

    async def issue_session(self, user: User) -> IssuedToken:
        raw = _random_token()
        expires = _now() + timedelta(minutes=settings.auth_session_ttl_minutes)
        token = AuthToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=_hash_token(raw),
            token_type=TOKEN_TYPE_SESSION,
            expires_at=expires,
        )
        self.db.add(token)
        await self.db.flush()
        return IssuedToken(raw=raw, token_id=token.id, expires_at=expires)

    async def consume_magic_link(self, raw_token: str) -> User | None:
        """Validates + marks the magic-link used, returns its User.

        Returns None if the token is unknown, expired, or already used.
        Constant-time enough for this application (we're not a bank).
        """
        now = _now()
        h = _hash_token(raw_token)
        row = (
            await self.db.execute(
                select(AuthToken).where(
                    AuthToken.token_hash == h,
                    AuthToken.token_type == TOKEN_TYPE_MAGIC,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.used_at is not None:
            return None
        if row.expires_at <= now:
            return None
        # Atomically mark used. We use an UPDATE ... WHERE used_at IS NULL
        # to defend against a concurrent second verify racing us.
        result = await self.db.execute(
            update(AuthToken)
            .where(AuthToken.id == row.id, AuthToken.used_at.is_(None))
            .values(used_at=now)
            .returning(AuthToken.id)
        )
        claimed = result.scalar_one_or_none()
        if claimed is None:
            # Lost the race — another verify got there first
            return None
        user = (
            await self.db.execute(select(User).where(User.id == row.user_id))
        ).scalar_one_or_none()
        return user

    async def resolve_session(self, raw_token: str) -> User | None:
        """Look up a live session token and return its user."""
        if not raw_token:
            return None
        now = _now()
        h = _hash_token(raw_token)
        row = (
            await self.db.execute(
                select(AuthToken).where(
                    AuthToken.token_hash == h,
                    AuthToken.token_type == TOKEN_TYPE_SESSION,
                )
            )
        ).scalar_one_or_none()
        if row is None or row.expires_at <= now:
            return None
        user = (
            await self.db.execute(select(User).where(User.id == row.user_id))
        ).scalar_one_or_none()
        return user

    async def revoke_session(self, raw_token: str) -> None:
        """Delete a session token (logout). No-op if unknown."""
        if not raw_token:
            return
        h = _hash_token(raw_token)
        await self.db.execute(
            update(AuthToken)
            .where(
                AuthToken.token_hash == h,
                AuthToken.token_type == TOKEN_TYPE_SESSION,
            )
            .values(expires_at=_now())
        )
