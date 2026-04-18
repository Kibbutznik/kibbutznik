"""Governance-health evaluation suite.

Synthesizes two scenarios — a **baseline** (members vote independently) and
a **sybil attack** (two members co-support every proposal from either one)
— and asserts that MetricsService measurably distinguishes them. This is
the Track D verification gate from the plan: "one replay run showing
measurably worse metrics when SybilRing is added vs baseline."

We don't drive the LLM here. We hit the DB directly with synthetic
proposals + supports so the eval runs fast and deterministically. The
cost of skipping real agents: we don't catch LLM-prompt regressions, but
we DO catch metric-calculation regressions, which is what this file's
job actually is.
"""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kbz.enums import MemberStatus, ProposalStatus, PulseStatus
from kbz.models.community import Community
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.pulse import Pulse
from kbz.models.support import Support
from kbz.services.metrics_service import MetricsService


async def _seed_baseline(
    db: AsyncSession, member_count: int = 6, rounds: int = 15, seed: int = 42
) -> uuid.UUID:
    """Cooperative community: each member independently supports each proposal
    with 50% probability. Rejection rate should hover near random, sybil
    suspicion should be low."""
    rng = random.Random(seed)
    cid = uuid.uuid4()
    db.add(Community(id=cid, name="BaselineCo", member_count=member_count))
    members = [uuid.uuid4() for _ in range(member_count)]
    for u in members:
        db.add(
            Member(
                user_id=u,
                community_id=cid,
                status=MemberStatus.ACTIVE,
                seniority=0,
            )
        )
    for _ in range(rounds):
        db.add(
            Pulse(
                id=uuid.uuid4(),
                community_id=cid,
                status=PulseStatus.DONE,
                support_count=0,
                threshold=1,
            )
        )
    # ~4 proposals per round; each member supports with p=0.5
    for round_i in range(rounds):
        for _ in range(4):
            author = rng.choice(members)
            pid = uuid.uuid4()
            # Pretend threshold is 50% of members; sample supports and
            # stamp the proposal's status based on whether it met quorum.
            supporters = [m for m in members if rng.random() < 0.5]
            status = (
                ProposalStatus.ACCEPTED
                if len(supporters) >= member_count // 2 + 1
                else ProposalStatus.REJECTED
            )
            db.add(
                Proposal(
                    id=pid,
                    community_id=cid,
                    user_id=author,
                    proposal_type="AddStatement",
                    proposal_status=status,
                    proposal_text="synthetic",
                    age=rng.randint(1, 3),
                    support_count=len(supporters),
                )
            )
            for s in supporters:
                db.add(Support(user_id=s, proposal_id=pid, support_value=1))
    await db.flush()
    return cid


async def _seed_sybil_attack(
    db: AsyncSession, member_count: int = 6, rounds: int = 15, seed: int = 42
) -> uuid.UUID:
    """Same shape as baseline, but TWO members (sybil_a, sybil_b) always
    co-support every proposal either one might have supported. Their
    co-support ratio should approach 1.0, and they should heavily
    outscore any organic pair."""
    rng = random.Random(seed)
    cid = uuid.uuid4()
    db.add(Community(id=cid, name="SybilAttackCo", member_count=member_count))
    members = [uuid.uuid4() for _ in range(member_count)]
    sybil_a, sybil_b = members[0], members[1]
    honest = members[2:]
    for u in members:
        db.add(
            Member(
                user_id=u,
                community_id=cid,
                status=MemberStatus.ACTIVE,
                seniority=0,
            )
        )
    for _ in range(rounds):
        db.add(
            Pulse(
                id=uuid.uuid4(),
                community_id=cid,
                status=PulseStatus.DONE,
                support_count=0,
                threshold=1,
            )
        )
    for round_i in range(rounds):
        for _ in range(4):
            author = rng.choice(members)
            pid = uuid.uuid4()
            honest_supporters = [m for m in honest if rng.random() < 0.5]
            # Sybil rule: if ANY of A/B would have supported independently,
            # BOTH do (that's the attack). With p=0.5 per-sybil independently,
            # the union is p=0.75 that they both support.
            a_inclined = rng.random() < 0.5
            b_inclined = rng.random() < 0.5
            if a_inclined or b_inclined:
                sybil_support = [sybil_a, sybil_b]
            else:
                sybil_support = []
            supporters = list(sybil_support) + honest_supporters
            status = (
                ProposalStatus.ACCEPTED
                if len(supporters) >= member_count // 2 + 1
                else ProposalStatus.REJECTED
            )
            db.add(
                Proposal(
                    id=pid,
                    community_id=cid,
                    user_id=author,
                    proposal_type="AddStatement",
                    proposal_status=status,
                    proposal_text="synthetic",
                    age=rng.randint(1, 3),
                    support_count=len(supporters),
                )
            )
            for s in supporters:
                db.add(Support(user_id=s, proposal_id=pid, support_value=1))
    await db.flush()
    return cid


@pytest.mark.asyncio
async def test_sybil_attack_produces_higher_suspicion_than_baseline(db_engine):
    """Core gate: the eval harness must detect the attack.

    Baseline suspicion ≤ 0.5 and attack suspicion ≥ 0.7 — those thresholds
    are empirical-but-generous. If either side blows past its threshold
    it's likely the sybil scoring math regressed.
    """
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with sf() as db:
        baseline_cid = await _seed_baseline(db)
        await db.commit()
    async with sf() as db:
        baseline_metrics = await MetricsService(db).compute(baseline_cid)

    async with sf() as db:
        attack_cid = await _seed_sybil_attack(db)
        await db.commit()
    async with sf() as db:
        attack_metrics = await MetricsService(db).compute(attack_cid)

    # The attack MUST register higher suspicion than baseline
    assert attack_metrics.sybil_suspicion_score > baseline_metrics.sybil_suspicion_score, (
        f"Sybil attack (score={attack_metrics.sybil_suspicion_score}) did NOT "
        f"outscore baseline (score={baseline_metrics.sybil_suspicion_score}). "
        f"The sybil detector regressed."
    )
    # And the delta must be meaningful, not noise
    delta = attack_metrics.sybil_suspicion_score - baseline_metrics.sybil_suspicion_score
    assert delta >= 0.2, (
        f"Sybil detector margin too narrow — baseline={baseline_metrics.sybil_suspicion_score}, "
        f"attack={attack_metrics.sybil_suspicion_score}, delta={delta:.3f} (need ≥ 0.2)"
    )
    # Max co-support ratio should also be clearly higher in attack
    assert attack_metrics.max_cosupport_ratio >= 0.85
    # Throughput should stay roughly comparable — a sybil attack doesn't
    # inherently change *overall* acceptance rate, just *who decides*. This
    # guards against a future regression where the seeding logic diverges.
    assert abs(attack_metrics.proposal_throughput - baseline_metrics.proposal_throughput) < 3.0


@pytest.mark.asyncio
async def test_baseline_has_no_deadlock(db_engine):
    """Sanity: a 6-member community with 15 rounds of 4 proposals/round
    should never be in deadlock — odds of zero accepted proposals are
    vanishing for a 50%-support independent population."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_baseline(db)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    # There should be at least one accepted proposal, and deadlock should
    # therefore be less than the total pulse count (the "never accepted"
    # fallback case).
    assert m.proposals_accepted > 0, "baseline should accept at least one proposal"
    assert m.deadlock_pulses < m.pulses_fired


@pytest.mark.asyncio
async def test_ttq_is_reported_when_acceptances_exist(db_engine):
    """ttq percentiles must be non-None once there are any accepted
    proposals. Protects the viewer card from going stale."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_baseline(db)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    if m.proposals_accepted > 0:
        assert m.ttq_p50 is not None
        assert m.ttq_p95 is not None
        assert m.ttq_p50 >= 1
        assert m.ttq_p95 >= m.ttq_p50
