"""Per-user notification fan-out + read API.

Inline-write design (NOT event_bus subscription): caller services
invoke `NotificationService.fanout_*` methods directly, in the same
DB transaction that produced the originating proposal/pulse/comment.
This means:

- Notifications and the originating row commit atomically. No race
  where a UI sees the proposal before the notification, or vice
  versa.
- Tests don't need to spin up a background worker — the read API
  finds the row immediately after the write.
- We trade out-of-process decoupling for transactional honesty,
  which matters for governance: "I never saw this proposal" is a
  serious complaint to defend against.

The translation rules from event → notifications live here so the
shape of the inbox stays in one file as we add more triggers
(comments, pulse outcomes, vote-missing reminders).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import MemberStatus
from kbz.models.member import Member
from kbz.models.notification import (
    KIND_COMMENT_POSTED,
    KIND_PROPOSAL_ACCEPTED,
    KIND_PROPOSAL_CANCELED,
    KIND_PROPOSAL_CREATED,
    KIND_PROPOSAL_REJECTED,
    Notification,
)


# Per-notification text-snippet cap. Keeps the JSONB blob small so
# the inbox query stays cheap even at thousands of rows.
_TEXT_SNIPPET_CAP = 280


def _snip(text: str | None) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= _TEXT_SNIPPET_CAP else (text[: _TEXT_SNIPPET_CAP - 1] + "…")


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── fan-out (write) ────────────────────────────────────────────

    async def fanout_proposal_created(
        self,
        *,
        community_id: uuid.UUID,
        proposal_id: uuid.UUID,
        proposal_type: str,
        proposal_text: str | None,
        author_user_id: uuid.UUID,
    ) -> int:
        """One row per active member of the community, EXCEPT the
        author — they're not notified about their own proposal.

        Returns the number of rows written. Caller owns the commit;
        we only flush so the originating service can decide whether
        to bundle this with its own outer transaction.
        """
        recipients = await self._active_members_excluding(
            community_id, author_user_id,
        )
        payload = {
            "proposal_id": str(proposal_id),
            "proposal_type": proposal_type,
            "proposal_text": _snip(proposal_text),
            "author_user_id": str(author_user_id),
        }
        return await self._insert_many(
            recipients, KIND_PROPOSAL_CREATED, community_id, payload,
        )

    async def fanout_proposal_outcome(
        self,
        *,
        community_id: uuid.UUID,
        proposal_id: uuid.UUID,
        proposal_type: str,
        proposal_text: str | None,
        author_user_id: uuid.UUID,
        outcome_kind: str,
    ) -> int:
        """Notify the proposal author when their proposal lands —
        Accepted, Rejected, or Canceled. Other listeners (supporters,
        fence-sitters) get added in a follow-up cycle once we land
        the SUPPORTED_BY join.

        `outcome_kind` must be one of the KIND_PROPOSAL_* constants
        for the three terminal states.
        """
        if outcome_kind not in (
            KIND_PROPOSAL_ACCEPTED,
            KIND_PROPOSAL_REJECTED,
            KIND_PROPOSAL_CANCELED,
        ):
            raise ValueError(f"unsupported outcome_kind: {outcome_kind!r}")
        payload = {
            "proposal_id": str(proposal_id),
            "proposal_type": proposal_type,
            "proposal_text": _snip(proposal_text),
        }
        return await self._insert_many(
            [author_user_id], outcome_kind, community_id, payload,
        )

    async def fanout_comment_posted(
        self,
        *,
        community_id: uuid.UUID | None,
        comment_id: uuid.UUID,
        commenter_user_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        comment_text: str | None,
        notify_user_id: uuid.UUID | None,
    ) -> int:
        """Tell `notify_user_id` (typically the proposal author or
        parent-comment author) that a new comment landed under their
        thing. Skip self-notifications — talking to yourself is fine,
        but it doesn't belong in the inbox."""
        if notify_user_id is None or notify_user_id == commenter_user_id:
            return 0
        payload = {
            "comment_id": str(comment_id),
            "commenter_user_id": str(commenter_user_id),
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "comment_text": _snip(comment_text),
        }
        return await self._insert_many(
            [notify_user_id], KIND_COMMENT_POSTED, community_id, payload,
        )

    # ── read API ───────────────────────────────────────────────────

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all())

    async def unread_count(self, user_id: uuid.UUID) -> int:
        from sqlalchemy import func
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def mark_read(
        self, user_id: uuid.UUID, notification_id: uuid.UUID,
    ) -> bool:
        """Returns True iff a row owned by user_id was actually flipped.
        False covers both 'already read' and 'belongs to someone else'
        — we deliberately don't distinguish those two so a fishing
        client can't probe for foreign notification ids."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now)
            .returning(Notification.id)
        )
        return result.scalar_one_or_none() is not None

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now)
            .returning(Notification.id)
        )
        return len(result.scalars().all())

    # ── internals ──────────────────────────────────────────────────

    async def _active_members_excluding(
        self, community_id: uuid.UUID, exclude_user_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        rows = await self.db.execute(
            select(Member.user_id).where(
                Member.community_id == community_id,
                Member.status == MemberStatus.ACTIVE,
                Member.user_id != exclude_user_id,
            )
        )
        return [row[0] for row in rows.all()]

    async def _insert_many(
        self,
        user_ids: list[uuid.UUID],
        kind: str,
        community_id: uuid.UUID | None,
        payload: dict,
    ) -> int:
        if not user_ids:
            return 0
        for uid in user_ids:
            self.db.add(
                Notification(
                    id=uuid.uuid4(),
                    user_id=uid,
                    community_id=community_id,
                    kind=kind,
                    payload_json=payload,
                )
            )
        await self.db.flush()
        return len(user_ids)
