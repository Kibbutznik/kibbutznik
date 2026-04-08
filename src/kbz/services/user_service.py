import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.models.user import User
from kbz.schemas.user import UserCreate


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: UserCreate) -> User:
        user = User(
            id=uuid.uuid4(),
            user_name=data.user_name,
            password_hash=_hash_password(data.password),
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
