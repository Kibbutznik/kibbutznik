"""Closeness — exponential moving average of pairwise agreement.

Each pair of active community members keeps a running score in roughly
[-1, +1]. After every scored proposal we update each pair's score by:

    signal   = +1 if both supported (or both abstained),  -1 otherwise
    weight   = 2 · s_p · (1 − s_p)             # informativeness,  peaks at 0.5
    score'   = (1 − α · weight) · score  +  (α · weight) · signal

α is the base update rate. `weight` gates by how informative the proposal
actually is: a near-unanimous vote tells us almost nothing about affinity,
so weight → 0 and the score barely moves. A 50/50 split is maximally
informative (weight = 0.5), and score moves by up to α · 0.5 toward the
signal. Bounded, no drift, self-normalizing — a pair that agrees 70% of the
time settles around +0.4 without accumulation games.

At community creation we also seed a few random positive pairs at +0.25 so
the graph isn't monochromatic at round 0 and some agents start "close" to
others — those seeds then evolve under the same EMA rule.
"""

from __future__ import annotations

import random
import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import MemberStatus
from kbz.models.closeness import Closeness
from kbz.models.member import Member
from kbz.models.support import Support


# --- Tuning knobs ---------------------------------------------------
# Base update rate — fraction of the gap between score and signal closed
# per maximally-informative proposal. 0.15 means ~15 proposals to fully
# converge; lower = slower drift, higher = more reactive.
_ALPHA = 0.15

# Minimum informativeness gate. Below this the proposal is skipped entirely
# (near-unanimous votes carry almost no pairwise signal).
_MIN_WEIGHT = 0.02

# Seed strength + count — how many initial "friend" pairs to stamp at
# community creation, and at what score.
_SEED_SCORE = 0.25
# Fraction of all possible pairs that start positive. For n=6 that's
# round(15 * 0.25) = 4 pairs out of 15 — enough to see clusters at round 0
# without making the graph mostly-green by default.
_SEED_FRACTION = 0.25


class ClosenessService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _ordered(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        return (a, b) if str(a) < str(b) else (b, a)

    async def _get_score(
        self, a: uuid.UUID, b: uuid.UUID
    ) -> float:
        u1, u2 = self._ordered(a, b)
        row = (
            await self.db.execute(
                select(Closeness.score).where(
                    Closeness.user_id1 == u1,
                    Closeness.user_id2 == u2,
                )
            )
        ).first()
        return float(row.score) if row else 0.0

    async def _set_score(
        self, a: uuid.UUID, b: uuid.UUID, score: float
    ) -> None:
        if a == b:
            return
        # Clamp to keep the EMA from ever leaving [-1, 1] due to floating
        # drift or a stray manual poke.
        score = max(-1.0, min(1.0, score))
        u1, u2 = self._ordered(a, b)
        stmt = (
            pg_insert(Closeness)
            .values(user_id1=u1, user_id2=u2, score=score)
            .on_conflict_do_update(
                index_elements=["user_id1", "user_id2"],
                set_={"score": score, "last_calculation": text("NOW()")},
            )
        )
        await self.db.execute(stmt)

    async def apply_proposal_outcome(
        self, community_id: uuid.UUID, proposal_id: uuid.UUID,
    ) -> None:
        """Update every active pair's EMA of agreement using this proposal."""
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

        k = sum(1 for uid in member_ids if uid in supporters)
        if k == 0 or k == n:
            return  # no pairwise signal from fully-unanimous proposals
        s_p = k / n
        weight = 2.0 * s_p * (1.0 - s_p)
        if weight < _MIN_WEIGHT:
            return
        step = _ALPHA * weight

        # EMA update per pair: score ← (1−step)·score + step·signal
        for i in range(n):
            a = member_ids[i]
            a_sup = a in supporters
            for j in range(i + 1, n):
                b = member_ids[j]
                b_sup = b in supporters
                signal = 1.0 if a_sup == b_sup else -1.0
                current = await self._get_score(a, b)
                new_score = (1.0 - step) * current + step * signal
                await self._set_score(a, b, new_score)

    async def seed_initial_pairs(
        self, member_ids: list[uuid.UUID], *, seed: int | None = None,
    ) -> int:
        """Stamp a few random pairs with a small positive score.

        Called once at community creation so the closeness graph isn't
        flat at round 0 — some agents start "close" and the EMA dynamics
        evolve from there. Returns the number of pairs seeded.
        """
        n = len(member_ids)
        if n < 2:
            return 0
        # Clean slate for this cohort: even with fresh UUIDs the EMA can
        # layer onto a stale extreme value if any row survived the age
        # purge (or if UUIDs get reused). Wipe every row whose BOTH
        # endpoints live in the incoming member set so seeds land on an
        # empty baseline and the heatmap starts genuinely blank.
        await self.db.execute(
            text(
                "DELETE FROM closeness_records "
                "WHERE user_id1 = ANY(:ids) AND user_id2 = ANY(:ids)"
            ),
            {"ids": list(member_ids)},
        )
        rng = random.Random(seed)
        all_pairs = [
            (member_ids[i], member_ids[j])
            for i in range(n)
            for j in range(i + 1, n)
        ]
        pick_count = max(1, int(round(len(all_pairs) * _SEED_FRACTION)))
        chosen = rng.sample(all_pairs, k=min(pick_count, len(all_pairs)))
        for a, b in chosen:
            # Small jitter so seeds aren't identical — keeps heatmap lively.
            s = _SEED_SCORE + rng.uniform(-0.05, 0.05)
            await self._set_score(a, b, s)
        return len(chosen)

    async def purge_stale_rows(self, older_than_hours: int = 12) -> int:
        """Delete rows whose last_calculation is older than the cutoff.

        Cheap hygiene for long-running servers: closeness for users from
        dead simulation runs hangs around forever otherwise.
        """
        # Use make_interval so asyncpg can bind :h as a plain int —
        # the `(:h || ' hours')::interval` form forces $1 to be a string
        # and blows up with "expected str, got int".
        result = await self.db.execute(
            text(
                "DELETE FROM closeness_records "
                "WHERE last_calculation < NOW() - make_interval(hours => :h)"
            ),
            {"h": int(older_than_hours)},
        )
        return result.rowcount or 0

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
