import re
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import ProposalType
from kbz.models.comment import Comment
from kbz.models.proposal import Proposal
from kbz.schemas.comment import CommentCreate
from kbz.services.event_bus import event_bus


_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", (text or "").lower()).strip()


def _comment_quotes_proposal(comment_text: str, proposal_text: str, window: int = 5) -> bool:
    """True if `comment_text` contains any `window`-word run from `proposal_text`.

    Strips quotes and punctuation noise via lower+collapse-whitespace. Requires
    a literal copied substring of >= `window` consecutive words from the
    proposal — defends against agents fabricating details that aren't in the
    proposal at all.
    """
    norm_comment = _normalize(comment_text)
    norm_proposal = _normalize(proposal_text)
    words = norm_proposal.split(" ")
    if len(words) < window:
        # Proposal too short to require a windowed quote — let it through.
        return True
    for i in range(len(words) - window + 1):
        run = " ".join(words[i : i + window])
        if run and run in norm_comment:
            return True
    return False


class CommentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_comment(
        self, entity_id: uuid.UUID, entity_type: str, data: CommentCreate
    ) -> Comment:
        # If this is a reply, the parent must exist AND be attached to the
        # same (entity_id, entity_type). Otherwise a reply can jump threads
        # — e.g. reply to a comment on proposal A while posting on proposal
        # B — and the tree view silently loses or duplicates the thread.
        if data.parent_comment_id is not None:
            parent = (
                await self.db.execute(
                    select(Comment).where(Comment.id == data.parent_comment_id)
                )
            ).scalar_one_or_none()
            if parent is None:
                raise HTTPException(status_code=404, detail="parent comment not found")
            if parent.entity_id != entity_id or parent.entity_type != entity_type:
                raise HTTPException(
                    status_code=400,
                    detail="parent comment belongs to a different entity",
                )
        # Anti-hallucination guard for EditArtifact proposal comments:
        # the comment must literally quote at least 5 consecutive words from
        # the proposal text, otherwise the agent is making it up.
        if entity_type == "proposal":
            prop = (
                await self.db.execute(select(Proposal).where(Proposal.id == entity_id))
            ).scalar_one_or_none()
            if prop and prop.proposal_type == ProposalType.EDIT_ARTIFACT.value:
                if not _comment_quotes_proposal(data.comment_text or "", prop.proposal_text or ""):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "EditArtifact comment must quote a 5+ word literal substring "
                            "from the proposal_text. Read the actual PROPOSED content and "
                            "quote a phrase from it — do not fabricate details."
                        ),
                    )
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
        # Emit for the TKG ingestor — open COMMENTED_ON edge + embed text.
        # community_id isn't carried on the Comment row, so we resolve it
        # best-effort from the target proposal when applicable.
        community_id = None
        if entity_type == "proposal":
            prop = (
                await self.db.execute(
                    select(Proposal.community_id).where(Proposal.id == entity_id)
                )
            ).scalar_one_or_none()
            community_id = prop
        elif entity_type == "community":
            community_id = entity_id
        await event_bus.emit(
            "comment.posted",
            community_id=community_id,
            user_id=data.user_id,
            comment_id=comment.id,
            entity_id=entity_id,
            entity_type=entity_type,
            comment_text=data.comment_text,
        )
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
