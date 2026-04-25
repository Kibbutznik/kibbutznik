"""FlagService — symmetric +1/-1 flags with closeness side-effect.

Each set/clear adjusts the closeness score between the flagger and
the *author* of the target by a small fixed amount. Flipping a flag
(say -1 → +1) reverses the prior delta before applying the new one,
so totals stay consistent across re-flagging.

Closeness mechanics:
- A positive flag nudges score toward +1 by FLAG_CLOSENESS_STEP.
- A negative flag nudges score toward -1 by the same step.
- Score is clamped to [-1, +1] (the existing `_set_score` helper
  does this for us).
- Self-flags (flagger == author) are rejected — a closeness loop
  has no semantics.

Author resolution per target_kind:
- "comment"  → comments.user_id
- "proposal" → proposals.user_id
- "reason"   → reasons.user_id
- "user"     → target_id is the author (flagging the person directly)
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.models.comment import Comment
from kbz.models.flag import (
    Flag,
    TARGET_COMMENT, TARGET_PROPOSAL, TARGET_REASON, TARGET_USER, TARGET_KINDS,
    VALUE_NEGATIVE, VALUE_POSITIVE, VALUES,
)
from kbz.models.proposal import Proposal
from kbz.models.reason import Reason
from kbz.models.user import User
from kbz.services.closeness_service import ClosenessService
from kbz.services.member_service import MemberService


# How much a single flag moves the pairwise closeness score. Smaller
# than the proposal-outcome EMA step (~0.075 effective) on purpose —
# a flag is one click, much lighter than co-voting on a proposal.
FLAG_CLOSENESS_STEP = 0.05


class FlagService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---- author resolution ---------------------------------------

    async def _resolve_author(
        self, target_kind: str, target_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Find the user who authored the target (or is the target,
        for `user` flags). Returns None if the target doesn't exist."""
        if target_kind == TARGET_USER:
            row = (
                await self.db.execute(
                    select(User.id).where(User.id == target_id)
                )
            ).scalar_one_or_none()
            return row
        if target_kind == TARGET_COMMENT:
            return (
                await self.db.execute(
                    select(Comment.user_id).where(Comment.id == target_id)
                )
            ).scalar_one_or_none()
        if target_kind == TARGET_PROPOSAL:
            return (
                await self.db.execute(
                    select(Proposal.user_id).where(Proposal.id == target_id)
                )
            ).scalar_one_or_none()
        if target_kind == TARGET_REASON:
            return (
                await self.db.execute(
                    select(Reason.user_id).where(Reason.id == target_id)
                )
            ).scalar_one_or_none()
        return None

    # ---- closeness side effect -----------------------------------

    async def _apply_closeness_delta(
        self,
        flagger_user_id: uuid.UUID,
        author_user_id: uuid.UUID,
        delta_sign: int,
    ) -> None:
        """delta_sign in {+1, -1, +2, -2} — magnitude 2 happens when
        re-flagging from the opposite direction (prior delta is
        reversed in the same call)."""
        if flagger_user_id == author_user_id:
            return
        closeness = ClosenessService(self.db)
        current = await closeness._get_score(flagger_user_id, author_user_id)
        new_score = current + (delta_sign * FLAG_CLOSENESS_STEP)
        await closeness._set_score(flagger_user_id, author_user_id, new_score)

    # ---- public API ----------------------------------------------

    async def set_flag(
        self,
        *,
        flagger_user_id: uuid.UUID,
        community_id: uuid.UUID,
        target_kind: str,
        target_id: uuid.UUID,
        value: int,
    ) -> Flag:
        """Create or replace the flagger's flag on this target.

        Side effect: applies a closeness delta. If the flag is new,
        delta = sign(value). If it's a flip (e.g. -1 → +1), the prior
        delta is reversed AND the new one applied (net 2× sign(value)).
        """
        if target_kind not in TARGET_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"target_kind must be one of {list(TARGET_KINDS)}",
            )
        if value not in VALUES:
            raise HTTPException(
                status_code=422, detail="value must be -1 or 1",
            )

        # Membership check: the flagger has to be an active member
        # of the community they're flagging in.
        if not await MemberService(self.db).is_active_member(
            community_id, flagger_user_id,
        ):
            raise HTTPException(
                status_code=403,
                detail="Only active members of this community can flag here",
            )

        author_id = await self._resolve_author(target_kind, target_id)
        if author_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"{target_kind} {target_id} not found",
            )

        # Self-flagging is meaningless and would create closeness self-loops.
        if author_id == flagger_user_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot flag your own content",
            )

        # Look up any existing flag from this user on this target.
        existing = (
            await self.db.execute(
                select(Flag).where(
                    Flag.flagger_user_id == flagger_user_id,
                    Flag.target_kind == target_kind,
                    Flag.target_id == target_id,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            # Brand new flag — apply +1 step in the value's direction.
            flag = Flag(
                id=uuid.uuid4(),
                flagger_user_id=flagger_user_id,
                community_id=community_id,
                target_kind=target_kind,
                target_id=target_id,
                value=value,
            )
            self.db.add(flag)
            await self._apply_closeness_delta(
                flagger_user_id, author_id, delta_sign=value,
            )
            await self.db.commit()
            await self.db.refresh(flag)
            return flag

        if existing.value == value:
            # No-op re-set. Closeness already reflects this flag.
            return existing

        # Flip: -1 → +1 or +1 → -1. Reverse the OLD direction's
        # contribution AND apply the NEW direction. Net delta is 2×
        # the new value's sign.
        existing.value = value
        await self._apply_closeness_delta(
            flagger_user_id, author_id, delta_sign=2 * value,
        )
        await self.db.commit()
        await self.db.refresh(existing)
        return existing

    async def clear_flag(
        self,
        *,
        flagger_user_id: uuid.UUID,
        target_kind: str,
        target_id: uuid.UUID,
    ) -> bool:
        """Remove the flagger's flag and reverse its closeness
        contribution. Returns True if a flag was removed, False if
        nothing to clear."""
        existing = (
            await self.db.execute(
                select(Flag).where(
                    Flag.flagger_user_id == flagger_user_id,
                    Flag.target_kind == target_kind,
                    Flag.target_id == target_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            return False

        author_id = await self._resolve_author(target_kind, target_id)
        if author_id is not None:
            # Reverse the prior contribution: opposite sign of the
            # value we previously applied.
            await self._apply_closeness_delta(
                flagger_user_id, author_id, delta_sign=-existing.value,
            )

        await self.db.execute(delete(Flag).where(Flag.id == existing.id))
        await self.db.commit()
        return True

    # ---- read API ------------------------------------------------

    async def get_summary(
        self,
        *,
        target_kind: str,
        target_id: uuid.UUID,
        viewer_user_id: uuid.UUID | None = None,
    ) -> dict:
        """Aggregate counts plus the viewer's own flag (if any).

        Does not require auth — the counts are public so the dashboard
        can render them on every comment without a logged-in roundtrip.
        Viewer's own value is None when no viewer or no flag.
        """
        if target_kind not in TARGET_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"target_kind must be one of {list(TARGET_KINDS)}",
            )
        rows = (
            await self.db.execute(
                select(Flag.value, func.count(Flag.id))
                .where(
                    Flag.target_kind == target_kind,
                    Flag.target_id == target_id,
                )
                .group_by(Flag.value)
            )
        ).all()
        counts = {int(v): int(c) for v, c in rows}

        my_value: int | None = None
        if viewer_user_id is not None:
            my_value = (
                await self.db.execute(
                    select(Flag.value).where(
                        Flag.flagger_user_id == viewer_user_id,
                        Flag.target_kind == target_kind,
                        Flag.target_id == target_id,
                    )
                )
            ).scalar_one_or_none()

        return {
            "target_kind": target_kind,
            "target_id": str(target_id),
            "positive": counts.get(VALUE_POSITIVE, 0),
            "negative": counts.get(VALUE_NEGATIVE, 0),
            "my_value": my_value,
        }

    async def list_my_flags(
        self,
        *,
        flagger_user_id: uuid.UUID,
        community_id: uuid.UUID | None = None,
    ) -> list[Flag]:
        """All flags the user has placed, optionally scoped to one
        community. Used by the dashboard's "what have I flagged"
        view."""
        stmt = select(Flag).where(Flag.flagger_user_id == flagger_user_id)
        if community_id is not None:
            stmt = stmt.where(Flag.community_id == community_id)
        stmt = stmt.order_by(Flag.created_at.desc())
        rows = (await self.db.execute(stmt)).scalars().all()
        return list(rows)
