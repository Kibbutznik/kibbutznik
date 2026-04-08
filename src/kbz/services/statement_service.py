import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import StatementStatus
from kbz.models.statement import Statement


class StatementService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_community(self, community_id: uuid.UUID) -> list[Statement]:
        result = await self.db.execute(
            select(Statement).where(
                Statement.community_id == community_id,
                Statement.status == StatementStatus.ACTIVE,
            ).order_by(Statement.created_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, statement_id: uuid.UUID) -> Statement | None:
        result = await self.db.execute(
            select(Statement).where(Statement.id == statement_id)
        )
        return result.scalar_one_or_none()
