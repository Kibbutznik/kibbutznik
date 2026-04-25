import re
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import ProposalType
from kbz.models.comment import Comment
from kbz.models.comment_vote import CommentVote
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

        # Inbox: notify the relevant user. For a top-level comment on
        # a proposal that's the proposal author. For a reply we also
        # bubble up to the parent comment's author. Self-comments are
        # filtered out inside NotificationService.
        notify_user_id: uuid.UUID | None = None
        notify_community_id: uuid.UUID | None = None
        if data.parent_comment_id is not None:
            parent = (
                await self.db.execute(
                    select(Comment).where(Comment.id == data.parent_comment_id)
                )
            ).scalar_one_or_none()
            if parent is not None:
                notify_user_id = parent.user_id
        if notify_user_id is None and entity_type == "proposal":
            prop_row = (
                await self.db.execute(
                    select(Proposal.user_id, Proposal.community_id).where(
                        Proposal.id == entity_id
                    )
                )
            ).first()
            if prop_row is not None:
                notify_user_id = prop_row[0]
                notify_community_id = prop_row[1]
        elif entity_type == "proposal":
            # Already filled notify_user_id via parent_comment_id;
            # still grab community_id for scoping.
            prop_cid = (
                await self.db.execute(
                    select(Proposal.community_id).where(Proposal.id == entity_id)
                )
            ).scalar_one_or_none()
            notify_community_id = prop_cid
        if notify_user_id is not None:
            from kbz.services.notification_service import NotificationService
            await NotificationService(self.db).fanout_comment_posted(
                community_id=notify_community_id,
                comment_id=comment.id,
                commenter_user_id=data.user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                comment_text=data.comment_text,
                notify_user_id=notify_user_id,
            )

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
        include_replies: bool = True,
    ) -> list[Comment]:
        """Return comments on an entity.

        `include_replies=True` (default): returns the full tree as a
        flat list. The client groups by `parent_comment_id` to render
        threading. This is what the proposal-detail / zoomed-comment
        modal needs — without it, replies are never sent over the wire
        and the threaded view always looks empty under each parent.

        `include_replies=False`: legacy behavior — root comments only.
        Useful for compact summary widgets that don't render threads.
        """
        query = select(Comment).where(
            Comment.entity_id == entity_id,
            Comment.entity_type == entity_type,
        )
        if not include_replies:
            query = query.where(Comment.parent_comment_id.is_(None))
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

    async def cast_vote(
        self,
        comment_id: uuid.UUID,
        user_id: uuid.UUID,
        delta: int,
    ) -> tuple[int, int | None]:
        """Toggle-aware vote cast.

        Pre-fix `update_score` blindly added `delta` to comment.score
        with no per-user dedupe — pressing the up arrow 20 times added
        20 points. Now backed by `comment_votes` (one row per (user,
        comment)). Semantics:

            - No prior vote, click up   → INSERT +1, score += 1
            - No prior vote, click down → INSERT -1, score -= 1
            - Prior +1, click up        → DELETE (toggle off), score -= 1
            - Prior -1, click down      → DELETE (toggle off), score += 1
            - Prior +1, click down      → UPDATE to -1 (flip), score -= 2
            - Prior -1, click up        → UPDATE to +1 (flip), score += 2

        Returns `(new_score, my_value_after)` where `my_value_after`
        is None when the click resulted in toggle-off.
        """
        if delta not in (1, -1):
            raise HTTPException(status_code=422, detail="delta must be -1 or 1")

        # Confirm the comment exists up front so the 404 is clean
        # rather than depending on a downstream UPDATE returning 0 rows.
        comment_score = (
            await self.db.execute(
                select(Comment.score).where(Comment.id == comment_id)
            )
        ).scalar_one_or_none()
        if comment_score is None:
            raise HTTPException(status_code=404, detail="Comment not found")

        existing = (
            await self.db.execute(
                select(CommentVote).where(
                    CommentVote.user_id == user_id,
                    CommentVote.comment_id == comment_id,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            # Brand new vote.
            self.db.add(
                CommentVote(
                    user_id=user_id, comment_id=comment_id, value=delta,
                )
            )
            score_delta = delta
            my_value_after: int | None = delta
        elif existing.value == delta:
            # Toggle off — same direction click cancels the vote.
            await self.db.execute(
                sa_delete(CommentVote).where(
                    CommentVote.user_id == user_id,
                    CommentVote.comment_id == comment_id,
                )
            )
            score_delta = -existing.value
            my_value_after = None
        else:
            # Flip from +1 → -1 or -1 → +1. Net delta is 2× the new
            # direction (cancel old contribution + apply new).
            existing.value = delta
            score_delta = 2 * delta
            my_value_after = delta

        result = await self.db.execute(
            update(Comment)
            .where(Comment.id == comment_id)
            .values(score=Comment.score + score_delta)
            .returning(Comment.score)
        )
        new_score = result.scalar_one()
        await self.db.commit()
        return int(new_score), my_value_after

    async def get_my_vote(
        self, comment_id: uuid.UUID, user_id: uuid.UUID,
    ) -> int | None:
        """Return the viewer's current vote on this comment (or None).
        Used by the dashboard to highlight the up/down arrow that's
        already cast."""
        return (
            await self.db.execute(
                select(CommentVote.value).where(
                    CommentVote.user_id == user_id,
                    CommentVote.comment_id == comment_id,
                )
            )
        ).scalar_one_or_none()

    async def get_my_votes_bulk(
        self, comment_ids: list[uuid.UUID], user_id: uuid.UUID,
    ) -> dict[uuid.UUID, int]:
        """Bulk lookup so the comment-list endpoint can stamp every
        row with `my_value` in one query instead of N+1."""
        if not comment_ids:
            return {}
        rows = (
            await self.db.execute(
                select(CommentVote.comment_id, CommentVote.value).where(
                    CommentVote.user_id == user_id,
                    CommentVote.comment_id.in_(comment_ids),
                )
            )
        ).all()
        return {cid: int(v) for cid, v in rows}
