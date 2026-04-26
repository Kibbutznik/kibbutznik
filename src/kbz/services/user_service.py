import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.models.user import User
from kbz.schemas.user import UserCreate


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: UserCreate) -> User:
        # NB: data.password is accepted (kept on the schema for
        # bot/test backwards compatibility) but DELIBERATELY DISCARDED
        # here. Auth in production goes through magic-link tokens
        # (auth_service.py); the password_hash column is never read
        # anywhere outside of legacy migrations. Pre-fix we stored
        # SHA-256(password) — unsalted, no key-stretching — which
        # turned every users table dump into a free credential leak
        # (rainbow tables make sha256 of common passwords instant).
        # Storing "" makes it impossible to leak a useful credential
        # from this column even if the DB is breached. If a real
        # password-auth path ever lands, it should use bcrypt/argon2
        # via auth_service, not this column.
        user = User(
            id=uuid.uuid4(),
            user_name=data.user_name,
            password_hash="",
            about=data.about,
            wallet_address=data.wallet_address,
        )
        self.db.add(user)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise
        await self.db.refresh(user)
        return user

    async def get(self, user_id: uuid.UUID) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, user_name: str) -> User | None:
        result = await self.db.execute(select(User).where(User.user_name == user_name))
        return result.scalar_one_or_none()
