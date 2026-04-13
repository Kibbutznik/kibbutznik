import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import MemberStatus
from kbz.models.closeness import Closeness
from kbz.models.member import Member
from kbz.models.support import Support


class ClosenessService:
    """Tracks affinity between members using entropy-weighted agreement scoring.

    For each proposal p with support rate s_p = supporters / members, each pair (A, B)
    of community members contributes:

        weight = 2 · s_p · (1 − s_p)   # peaks at 0.5, zero when unanimous
        both supported  →  + weight
        both abstained  →  + weight
        split support   →  − weight

    This gives equal positive/negative signals for agreement/disagreement, weighted
    by how informative the proposal is. Unanimous proposals carry near-zero weight
    (everyone agreed, no signal). Divisive proposals carry strong weight. This avoids
    the old covariance formula's bias toward negative scores in consensus communities.

    Scores accumulate as floats, can go negative, and are written incrementally at
    pulse execution time via Postgres upsert.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _ordered(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        return (a, b) if str(a) < str(b) else (b, a)

    async def _bump(self, user_a: uuid.UUID, user_b: uuid.UUID, delta: float) -> None:
        if user_a == user_b or delta == 0.0:
            return
        u1, u2 = self._ordered(user_a, user_b)
        stmt = pg_insert(Closeness).values(
            user_id1=u1, user_id2=u2, score=delta,
        ).on_conflict_do_update(
            index_elements=["user_id1", "user_id2"],
            set_={
                "score": Closeness.__table__.c.score + delta,
                "last_calculation": text("NOW()"),
            },
        )
        await self.db.execute(stmt)

    async def apply_proposal_outcome(
        self, community_id: uuid.UUID, proposal_id: uuid.UUID,
    ) -> None:
        """Update closeness for every pair of community members based on this proposal.

        Called once per proposal at pulse execution time (after its status is set).
        """
        members_result = await self.db.execute(
            select(Member.user_id).where(
                Member.community_id == community_id,
                Member.status == MemberStatus.ACTIVE,
            )
        )
        member_ids = [row[0] for row in members_result.all()]
        n = len(member_ids)
        if n < 2:
            return

        sup_result = await self.db.execute(
            select(Support.user_id).where(Support.proposal_id == proposal_id)
        )
        supporters = {row[0] for row in sup_result.all()}

        # Support rate for this proposal, clamped to (0, 1) open interval.
        # A fully unanimous or fully ignored proposal carries no affinity signal
        # for any pair, so we can skip it entirely.
        k = sum(1 for uid in member_ids if uid in supporters)
        if k == 0 or k == n:
            return
        s_p = k / n

        # Entropy-weighted ±1 signal: weight peaks at s_p=0.5, vanishes at 0 or 1
        weight = 2.0 * s_p * (1.0 - s_p)
        if weight < 0.01:
            return  # near-unanimous, no meaningful signal

        agree_delta = weight
        split_delta = -weight

        for i in range(n):
            a = member_ids[i]
            a_sup = a in supporters
            for j in range(i + 1, n):
                b = member_ids[j]
                b_sup = b in supporters
                if a_sup == b_sup:
                    await self._bump(a, b, agree_delta)
                else:
                    await self._bump(a, b, split_delta)

    async def get_pairs_for_users(self, user_ids: list[uuid.UUID]) -> list[dict]:
        """Return all closeness rows where BOTH endpoints are in the given user set."""
        if not user_ids:
            return []
        result = await self.db.execute(
            select(Closeness).where(
                Closeness.user_id1.in_(user_ids),
                Closeness.user_id2.in_(user_ids),
            )
        )
        rows = result.scalars().all()
        return [
            {
                "user_id1": str(r.user_id1),
                "user_id2": str(r.user_id2),
                "score": round(float(r.score), 3),
            }
            for r in rows
        ]
