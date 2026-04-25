"""Governance-health metrics service.

Reads from existing tables (no new schema) and produces a single
`CommunityMetrics` record summarizing how healthy a community's
governance has been. Used by:
  - `/metrics/community/{id}` router (viewer Metrics tab)
  - `tests/eval/test_governance_health.py` eval suite
  - `scripts/replay.py` for A/B on variables

Everything here is deliberately simple aggregation — no ML, no state.
The replay harness runs a simulation and then calls into here to
compare before/after.

## What each metric means

- `pulses_fired` — number of DONE pulses, i.e. completed rounds.
  All other "per round" figures normalize by this.

- `proposal_throughput` — accepted proposals per completed pulse.
  Low = the community can't get to yes. Sustainable communities
  land somewhere in [0.5, 3.0]; < 0.2 is a deadlock indicator.

- `rejection_rate` — rejected / (accepted + rejected). Excludes
  Canceled (aged-out) and in-flight proposals. 0.0–0.4 is healthy;
  > 0.6 means the community is blocking itself.

- `closeness_mean` / `closeness_stddev` — aggregated over the
  closeness_records for this community's members. A stddev near 0
  means everyone agrees (or nobody does); higher stddev = real
  coalitions. Mean drifts toward the community's average agreement.

- `sybil_suspicion_score` — max pairwise co-support ratio,
  scaled so 0.0 = no co-support and 1.0 = two members agree on
  EVERY proposal they've both voted on. Flags coordinated voting
  regardless of *intent* — legitimate allies also score high, but
  this combined with closeness_stddev is a useful signal.

- `deadlock_pulses` — pulses completed since the last proposal was
  accepted. 0 means the most recent pulse accepted something; 5+
  means the community is stuck.

- `time_to_quorum_p50` / `time_to_quorum_p95` — median / 95th
  percentile of `proposal.age` at acceptance (in pulse units).
  Measures how many pulses a successful proposal takes to go from
  creation to acceptance. Lower = healthier.
"""

from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import MemberStatus, ProposalStatus, PulseStatus
from kbz.models.closeness import Closeness
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.pulse import Pulse
from kbz.models.support import Support


@dataclass
class CommunityMetrics:
    community_id: str
    pulses_fired: int
    member_count: int

    # Throughput / rejection
    proposals_total: int
    proposals_accepted: int
    proposals_rejected: int
    proposals_canceled: int
    proposals_in_flight: int  # Draft + OutThere + OnTheAir
    proposal_throughput: float  # accepted / pulses_fired
    rejection_rate: float  # rejected / (accepted + rejected)

    # Cohesion
    closeness_pairs: int
    closeness_mean: float
    closeness_stddev: float

    # Co-support / sybil signal
    max_cosupport_ratio: float  # 0..1, tightest pair
    sybil_suspicion_score: float  # derived — see _sybil_score

    # Deadlock
    deadlock_pulses: int  # pulses since last acceptance

    # Time-to-quorum (proposal.age at acceptance)
    ttq_p50: float | None
    ttq_p95: float | None

    def as_dict(self) -> dict:
        return asdict(self)


class MetricsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def compute(self, community_id: uuid.UUID | str) -> CommunityMetrics:
        cid = community_id if isinstance(community_id, uuid.UUID) else uuid.UUID(str(community_id))

        # --- Member count (ACTIVE only) -----------------------------------
        member_count = int(
            (
                await self.db.execute(
                    select(func.count(Member.user_id)).where(
                        Member.community_id == cid,
                        Member.status == MemberStatus.ACTIVE,
                    )
                )
            ).scalar_one()
        )

        # --- Pulses fired (status = DONE) ---------------------------------
        pulses_fired = int(
            (
                await self.db.execute(
                    select(func.count(Pulse.id)).where(
                        Pulse.community_id == cid,
                        Pulse.status == PulseStatus.DONE,
                    )
                )
            ).scalar_one()
        )

        # --- Proposal counts by status ------------------------------------
        status_rows = (
            await self.db.execute(
                select(Proposal.proposal_status, func.count(Proposal.id))
                .where(Proposal.community_id == cid)
                .group_by(Proposal.proposal_status)
            )
        ).all()
        status_counts = {row[0]: row[1] for row in status_rows}
        accepted = status_counts.get(ProposalStatus.ACCEPTED, 0)
        rejected = status_counts.get(ProposalStatus.REJECTED, 0)
        canceled = status_counts.get(ProposalStatus.CANCELED, 0)
        in_flight = (
            status_counts.get(ProposalStatus.DRAFT, 0)
            + status_counts.get(ProposalStatus.OUT_THERE, 0)
            + status_counts.get(ProposalStatus.ON_THE_AIR, 0)
        )
        proposals_total = sum(status_counts.values())

        throughput = accepted / pulses_fired if pulses_fired > 0 else 0.0
        rejection_rate = (
            rejected / (accepted + rejected) if (accepted + rejected) > 0 else 0.0
        )

        # --- Closeness cohesion -------------------------------------------
        member_ids = (
            await self.db.execute(
                select(Member.user_id).where(
                    Member.community_id == cid,
                    Member.status == MemberStatus.ACTIVE,
                )
            )
        ).scalars().all()
        member_set = set(member_ids)

        # Only aggregate closeness rows where BOTH endpoints are current members,
        # otherwise stale rows from dead cohorts pollute the mean.
        if member_set:
            closeness_rows = (
                await self.db.execute(
                    select(Closeness.score).where(
                        Closeness.user_id1.in_(member_set),
                        Closeness.user_id2.in_(member_set),
                    )
                )
            ).scalars().all()
        else:
            closeness_rows = []

        closeness_scores = [float(s) for s in closeness_rows]
        closeness_pairs = len(closeness_scores)
        closeness_mean = statistics.fmean(closeness_scores) if closeness_scores else 0.0
        closeness_stddev = (
            statistics.pstdev(closeness_scores) if len(closeness_scores) > 1 else 0.0
        )

        # --- Sybil suspicion (max pairwise co-support ratio) --------------
        max_cosupport, sybil_score = await self._sybil_score(cid, member_set)

        # --- Deadlock (pulses since last accepted proposal) ---------------
        deadlock = await self._deadlock_pulses(cid, pulses_fired)

        # --- Time-to-quorum percentiles -----------------------------------
        ttq_p50, ttq_p95 = await self._time_to_quorum(cid)

        return CommunityMetrics(
            community_id=str(cid),
            pulses_fired=pulses_fired,
            member_count=member_count,
            proposals_total=proposals_total,
            proposals_accepted=accepted,
            proposals_rejected=rejected,
            proposals_canceled=canceled,
            proposals_in_flight=in_flight,
            proposal_throughput=round(throughput, 3),
            rejection_rate=round(rejection_rate, 3),
            closeness_pairs=closeness_pairs,
            closeness_mean=round(closeness_mean, 3),
            closeness_stddev=round(closeness_stddev, 3),
            max_cosupport_ratio=round(max_cosupport, 3),
            sybil_suspicion_score=round(sybil_score, 3),
            deadlock_pulses=deadlock,
            ttq_p50=round(ttq_p50, 2) if ttq_p50 is not None else None,
            ttq_p95=round(ttq_p95, 2) if ttq_p95 is not None else None,
        )

    # ---- helpers -----------------------------------------------------

    async def _sybil_score(
        self, community_id: uuid.UUID, member_set: set
    ) -> tuple[float, float]:
        """Return (max_cosupport_ratio, sybil_suspicion_score).

        For every pair of members that both supported at least MIN_SHARED
        proposals in common, compute `shared / min(|supports_u1|, |supports_u2|)`.
        Max across pairs = "tightest" co-support. Sybil score scales this by
        how many proposals the pair has had to co-support on — a 3/3 ratio
        on 3 proposals is much weaker evidence than 20/20 on 20.
        """
        if len(member_set) < 2:
            return 0.0, 0.0

        # All supports for this community's proposals, per-user list of proposal_ids
        rows = (
            await self.db.execute(
                select(Support.user_id, Support.proposal_id)
                .join(Proposal, Proposal.id == Support.proposal_id)
                .where(Proposal.community_id == community_id)
                .where(Support.user_id.in_(member_set))
            )
        ).all()
        per_user: dict = {}
        for user_id, proposal_id in rows:
            per_user.setdefault(user_id, set()).add(proposal_id)

        ids = [u for u in per_user if len(per_user[u]) >= 2]
        max_ratio = 0.0
        best_shared = 0
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = per_user[ids[i]], per_user[ids[j]]
                shared = len(a & b)
                smaller = min(len(a), len(b))
                if shared < 2 or smaller < 2:
                    continue
                ratio = shared / smaller
                if ratio > max_ratio or (ratio == max_ratio and shared > best_shared):
                    max_ratio = ratio
                    best_shared = shared

        # Suspicion score: ratio × sqrt(shared_count) / sqrt(20). Caps near 1.0
        # when a pair has ~20+ perfectly co-supported proposals.
        suspicion = max_ratio * min(1.0, math.sqrt(best_shared) / math.sqrt(20))
        return max_ratio, suspicion

    async def _deadlock_pulses(self, community_id: uuid.UUID, total_pulses: int) -> int:
        """Pulses elapsed since the most recent acceptance.

        Anchor on the proposal's DECISION timestamp, not creation time. A
        proposal filed on day 0 and accepted on day 5 should count
        "deadlock" from day 5, not day 0 — using created_at would count
        every intervening pulse as deadlocked even though the community
        was actively deciding. `decided_at` ships from the audit-log
        feature; legacy rows with NULL fall back to created_at via
        COALESCE so a partially-backfilled DB still returns a sane
        number.

        If no accepted proposals exist, deadlock = total_pulses.
        """
        decision_ts = func.coalesce(Proposal.decided_at, Proposal.created_at)
        last_accepted_ts = (
            await self.db.execute(
                select(func.max(decision_ts)).where(
                    Proposal.community_id == community_id,
                    Proposal.proposal_status == ProposalStatus.ACCEPTED,
                )
            )
        ).scalar()
        if last_accepted_ts is None:
            return total_pulses
        pulses_since = int(
            (
                await self.db.execute(
                    select(func.count(Pulse.id)).where(
                        Pulse.community_id == community_id,
                        Pulse.status == PulseStatus.DONE,
                        Pulse.created_at > last_accepted_ts,
                    )
                )
            ).scalar_one()
        )
        return pulses_since

    async def _time_to_quorum(
        self, community_id: uuid.UUID
    ) -> tuple[float | None, float | None]:
        """p50 / p95 of `proposal.age` across ACCEPTED proposals.

        `age` is incremented once per pulse tick while the proposal is
        OutThere. Once it goes OnTheAir (which happens in the same tick it
        reaches quorum) the age stops advancing, so `age at Accepted` =
        pulses-to-quorum. Perfect for health tracking.
        """
        ages = (
            await self.db.execute(
                select(Proposal.age).where(
                    Proposal.community_id == community_id,
                    Proposal.proposal_status == ProposalStatus.ACCEPTED,
                )
            )
        ).scalars().all()
        ages = [int(a) for a in ages if a is not None]
        if not ages:
            return None, None
        ages.sort()
        p50 = statistics.median(ages)
        # Simple nearest-rank p95
        p95_idx = max(0, min(len(ages) - 1, int(math.ceil(0.95 * len(ages))) - 1))
        p95 = float(ages[p95_idx])
        return float(p50), p95
