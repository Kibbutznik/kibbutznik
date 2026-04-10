import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.models.comment import Comment
from kbz.schemas.comment import CommentCreate


class CommentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_comment(
        self, entity_id: uuid.UUID, entity_type: str, data: CommentCreate
    ) -> Comment:
        comment = Comment(
            id=uuid.uuid4(),
            entity_id=entity_id,
            entity_type=entity_type,
            user_id=data.user_id,
            comment_text=data.comment_text,
            parent_comment_id=data.parent_comment_id,
            score=0,
        )
        self.db.add(comment)
        await self.db.commit()
        await self.db.refresh(comment)
        return comment

    async def get_comments(
        self,
        entity_id: uuid.UUID,
        entity_type: str,
        *,
        limit: int | None = None,
        after: datetime | None = None,
    ) -> list[Comment]:
        query = select(Comment).where(
            Comment.entity_id == entity_id,
            Comment.entity_type == entity_type,
            Comment.parent_comment_id.is_(None),
        )
        if after is not None:
            query = query.where(Comment.created_at > after)
        # Chat (community entity_type) uses chronological order;
        # proposal comments keep the existing score-based order.
        if entity_type == "community":
            query = query.order_by(Comment.created_at.desc())
        else:
            query = query.order_by(Comment.score.desc(), Comment.created_at.desc())
        if limit is not None:
            query = query.limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_replies(self, comment_id: uuid.UUID) -> list[Comment]:
        result = await self.db.execute(
            select(Comment)
            .where(Comment.parent_comment_id == comment_id)
            .order_by(Comment.score.desc())
        )
        return list(result.scalars().all())

    async def update_score(self, comment_id: uuid.UUID, delta: int) -> None:
        await self.db.execute(
            update(Comment)
            .where(Comment.id == comment_id)
            .values(score=Comment.score + delta)
        )
        await self.db.commit()
