"""Service layer for agent memory CRUD operations."""

import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.models.agent_memory import AgentMemory


class MemoryService:
    """Wraps database queries for the agent_memories table."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_memory(
        self,
        user_id: uuid.UUID,
        memory_type: str,
        content: str,
        importance: float = 0.5,
        category: str | None = None,
        round_num: int | None = None,
        related_id: uuid.UUID | None = None,
        expires_at: int | None = None,
    ) -> dict:
        """Insert a new memory record and return it as a dict."""
        mem = AgentMemory(
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            importance=importance,
            category=category,
            round_num=round_num,
            related_id=related_id,
            expires_at=expires_at,
        )
        self.db.add(mem)
        await self.db.commit()
        await self.db.refresh(mem)
        return self._to_dict(mem)

    async def get_memories(
        self,
        user_id: uuid.UUID,
        memory_type: str | None = None,
        limit: int = 20,
        min_importance: float = 0.0,
        order_by: str = "recent",  # "recent" | "importance"
    ) -> list[dict]:
        """Retrieve memories for a user with optional filters."""
        stmt = select(AgentMemory).where(AgentMemory.user_id == user_id)
        if memory_type:
            stmt = stmt.where(AgentMemory.memory_type == memory_type)
        if min_importance > 0:
            stmt = stmt.where(AgentMemory.importance >= min_importance)

        if order_by == "importance":
            stmt = stmt.order_by(AgentMemory.importance.desc(), AgentMemory.created_at.desc())
        else:
            stmt = stmt.order_by(AgentMemory.round_num.desc().nulls_last(), AgentMemory.created_at.desc())

        stmt = stmt.limit(limit)
        result = await self.db.execute(stmt)
        return [self._to_dict(m) for m in result.scalars().all()]

    async def update_memory(
        self,
        memory_id: uuid.UUID,
        **fields: Any,
    ) -> dict | None:
        """Update specific fields of a memory record."""
        allowed = {"content", "importance", "category", "expires_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return None

        await self.db.execute(
            update(AgentMemory)
            .where(AgentMemory.id == memory_id)
            .values(**updates)
        )
        await self.db.commit()

        result = await self.db.execute(
            select(AgentMemory).where(AgentMemory.id == memory_id)
        )
        mem = result.scalar_one_or_none()
        return self._to_dict(mem) if mem else None

    async def find_relationship(
        self,
        user_id: uuid.UUID,
        target_user_id: uuid.UUID,
    ) -> dict | None:
        """Find an existing relationship memory between two users."""
        result = await self.db.execute(
            select(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.memory_type == "relationship",
                AgentMemory.related_id == target_user_id,
            )
        )
        mem = result.scalar_one_or_none()
        return self._to_dict(mem) if mem else None

    async def prune(self, user_id: uuid.UUID, current_round: int) -> int:
        """Remove expired and excess memories. Returns count of deleted rows."""
        deleted = 0

        # 1. Delete expired memories
        result = await self.db.execute(
            delete(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.expires_at.isnot(None),
                AgentMemory.expires_at <= current_round,
            )
        )
        deleted += result.rowcount

        # 2. Delete old low-importance episodic (older than 30 rounds, importance < 0.4)
        if current_round > 30:
            result = await self.db.execute(
                delete(AgentMemory).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.memory_type == "episodic",
                    AgentMemory.round_num.isnot(None),
                    AgentMemory.round_num < current_round - 30,
                    AgentMemory.importance < 0.4,
                )
            )
            deleted += result.rowcount

        # 3. Cap episodic at 50 — delete oldest low-importance beyond limit
        deleted += await self._cap_type(user_id, "episodic", max_count=50)

        # 4. Cap goals at 20
        deleted += await self._cap_type(user_id, "goal", max_count=20)

        # 5. Cap relationships at 30
        deleted += await self._cap_type(user_id, "relationship", max_count=30)

        # 6. Keep only last 5 reflections
        deleted += await self._cap_type(user_id, "reflection", max_count=5)

        await self.db.commit()
        return deleted

    async def _cap_type(self, user_id: uuid.UUID, memory_type: str, max_count: int) -> int:
        """Delete excess memories of a given type, keeping the most important."""
        count_result = await self.db.execute(
            select(AgentMemory.id).where(
                AgentMemory.user_id == user_id,
                AgentMemory.memory_type == memory_type,
            )
        )
        all_ids = [row[0] for row in count_result.all()]
        if len(all_ids) <= max_count:
            return 0

        # Keep the top N by importance (ties broken by recency)
        keep_result = await self.db.execute(
            select(AgentMemory.id)
            .where(
                AgentMemory.user_id == user_id,
                AgentMemory.memory_type == memory_type,
            )
            .order_by(AgentMemory.importance.desc(), AgentMemory.created_at.desc())
            .limit(max_count)
        )
        keep_ids = {row[0] for row in keep_result.all()}
        delete_ids = [mid for mid in all_ids if mid not in keep_ids]

        if delete_ids:
            await self.db.execute(
                delete(AgentMemory).where(AgentMemory.id.in_(delete_ids))
            )
        return len(delete_ids)

    @staticmethod
    def _to_dict(mem: AgentMemory) -> dict:
        return {
            "id": str(mem.id),
            "user_id": str(mem.user_id),
            "memory_type": mem.memory_type,
            "category": mem.category,
            "content": mem.content,
            "importance": mem.importance,
            "round_num": mem.round_num,
            "related_id": str(mem.related_id) if mem.related_id else None,
            "expires_at": mem.expires_at,
            "created_at": mem.created_at.isoformat() if mem.created_at else None,
        }
