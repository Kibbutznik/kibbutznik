import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import CommunityStatus
from kbz.models.action import Action
from kbz.models.community import Community


class ActionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_parent(self, parent_community_id: uuid.UUID) -> list[dict]:
        query = (
            select(Action, Community.name)
            .join(Community, Community.id == Action.action_id)
            .where(
                Action.parent_community_id == parent_community_id,
                Action.status == CommunityStatus.ACTIVE,
            )
        )
        result = await self.db.execute(query)
        rows = result.all()
        return [
            {
                "action_id": str(row.Action.action_id),
                "parent_community_id": str(row.Action.parent_community_id),
                "status": row.Action.status,
                "name": row.name or "Unnamed",
            }
            for row in rows
        ]

    async def get(self, action_id: uuid.UUID) -> Action | None:
        result = await self.db.execute(
            select(Action).where(Action.action_id == action_id)
        )
        return result.scalar_one_or_none()
