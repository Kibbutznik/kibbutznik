"""MetricsService tests — seed state directly, assert the computed numbers.

We don't spin up agents here. We insert rows for members, proposals, supports,
pulses, and closeness_records that reflect what the live simulation produces,
and verify MetricsService rolls them up correctly. Also covers the edge cases
that bit us in the last scout pass — empty community, no pulses, no accepted
proposals.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kbz.enums import MemberStatus, ProposalStatus, PulseStatus
from kbz.models.closeness import Closeness
from kbz.models.community import Community
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.pulse import Pulse
from kbz.models.support import Support
from kbz.services.metrics_service import MetricsService


async def _seed_community(db: AsyncSession, name: str = "TestCo") -> uuid.UUID:
    cid = uuid.uuid4()
    db.add(Community(id=cid, name=name, member_count=0))
    await db.flush()
    return cid


async def _add_member(db: AsyncSession, cid: uuid.UUID, user_id: uuid.UUID) -> None:
    db.add(
        Member(
            user_id=user_id,
            community_id=cid,
            status=MemberStatus.ACTIVE,
            seniority=0,
        )
    )


async def _add_proposal(
    db: AsyncSession,
    cid: uuid.UUID,
    user_id: uuid.UUID,
    status: ProposalStatus,
    age: int = 0,
    ptype: str = "AddStatement",
) -> uuid.UUID:
    pid = uuid.uuid4()
    db.add(
        Proposal(
            id=pid,
            community_id=cid,
            user_id=user_id,
            proposal_type=ptype,
            proposal_status=status,
            proposal_text="test",
            age=age,
            support_count=0,
        )
    )
    return pid


async def _add_pulse(
    db: AsyncSession, cid: uuid.UUID, status: PulseStatus = PulseStatus.DONE
) -> uuid.UUID:
    pid = uuid.uuid4()
    db.add(
        Pulse(
            id=pid,
            community_id=cid,
            status=status,
            support_count=0,
            threshold=1,
        )
    )
    return pid


async def _add_support(
    db: AsyncSession, user_id: uuid.UUID, proposal_id: uuid.UUID
) -> None:
    db.add(Support(user_id=user_id, proposal_id=proposal_id, support_value=1))


async def _add_closeness(
    db: AsyncSession, u1: uuid.UUID, u2: uuid.UUID, score: float
) -> None:
    a, b = (u1, u2) if str(u1) < str(u2) else (u2, u1)
    db.add(Closeness(user_id1=a, user_id2=b, score=score))


@pytest.mark.asyncio
async def test_empty_community_returns_zero_metrics(db_engine):
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    assert m.member_count == 0
    assert m.pulses_fired == 0
    assert m.proposals_total == 0
    assert m.proposal_throughput == 0.0
    assert m.rejection_rate == 0.0
    assert m.closeness_pairs == 0
    assert m.ttq_p50 is None and m.ttq_p95 is None


@pytest.mark.asyncio
async def test_throughput_and_rejection(db_engine):
    """5 pulses, 3 accepted, 2 rejected → throughput 0.6, rejection 0.4."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        u = uuid.uuid4()
        await _add_member(db, cid, u)
        for _ in range(5):
            await _add_pulse(db, cid)
        for _ in range(3):
            await _add_proposal(db, cid, u, ProposalStatus.ACCEPTED, age=2)
        for _ in range(2):
            await _add_proposal(db, cid, u, ProposalStatus.REJECTED, age=3)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    assert m.pulses_fired == 5
    assert m.proposals_accepted == 3
    assert m.proposals_rejected == 2
    assert m.proposal_throughput == round(3 / 5, 3)
    assert m.rejection_rate == round(2 / 5, 3)
    assert m.ttq_p50 == 2.0
    assert m.ttq_p95 == 2.0


@pytest.mark.asyncio
async def test_sybil_detects_perfect_cosupport(db_engine):
    """2 members co-supporting 20 proposals in perfect lockstep → ratio 1.0,
    suspicion near 1.0. A third member supporting a subset should not
    dominate the max because their overlap is partial.
    """
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        sybil_a, sybil_b, honest = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await _add_member(db, cid, sybil_a)
        await _add_member(db, cid, sybil_b)
        await _add_member(db, cid, honest)
        for _ in range(20):
            pid = await _add_proposal(db, cid, honest, ProposalStatus.OUT_THERE)
            await _add_support(db, sybil_a, pid)
            await _add_support(db, sybil_b, pid)
            # honest supports half
            if _ % 2 == 0:
                await _add_support(db, honest, pid)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    assert m.max_cosupport_ratio == 1.0
    # 20 co-supports at ratio 1.0 should push suspicion to ~1.0
    assert m.sybil_suspicion_score >= 0.9


@pytest.mark.asyncio
async def test_sybil_score_low_when_sparse(db_engine):
    """Two members with only 2 shared supports should score low even at
    ratio 1.0 — evidence is too thin to flag."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        a, b = uuid.uuid4(), uuid.uuid4()
        await _add_member(db, cid, a)
        await _add_member(db, cid, b)
        for _ in range(2):
            pid = await _add_proposal(db, cid, a, ProposalStatus.OUT_THERE)
            await _add_support(db, a, pid)
            await _add_support(db, b, pid)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    # ratio = 1.0 but only 2 shared — suspicion should be well under 0.5
    assert m.max_cosupport_ratio == 1.0
    assert m.sybil_suspicion_score < 0.5


@pytest.mark.asyncio
async def test_closeness_aggregation_filters_nonmembers(db_engine):
    """Closeness rows with a non-member endpoint must be excluded (stale cohort)."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        a, b = uuid.uuid4(), uuid.uuid4()
        ghost = uuid.uuid4()  # NOT a member
        await _add_member(db, cid, a)
        await _add_member(db, cid, b)
        await _add_closeness(db, a, b, 0.5)
        await _add_closeness(db, a, ghost, -0.9)  # should be ignored
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    assert m.closeness_pairs == 1
    assert m.closeness_mean == 0.5


@pytest.mark.asyncio
async def test_deadlock_counts_pulses_after_last_acceptance(db_engine):
    """Seed an accepted proposal at time T, then 3 pulses at T+1s.
    Deadlock = 3. We manually set created_at to ensure strict ordering
    because SQLite's default `NOW()` can return the same value for rows
    flushed in the same transaction.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        u = uuid.uuid4()
        await _add_member(db, cid, u)
        base = datetime.now(timezone.utc)
        pid = await _add_proposal(db, cid, u, ProposalStatus.ACCEPTED, age=1)
        await db.flush()
        await db.execute(
            update(Proposal).where(Proposal.id == pid).values(created_at=base)
        )
        pulse_ids = []
        for _ in range(3):
            pulse_ids.append(await _add_pulse(db, cid))
        await db.flush()
        for i, pid_ in enumerate(pulse_ids):
            await db.execute(
                update(Pulse)
                .where(Pulse.id == pid_)
                .values(created_at=base + timedelta(seconds=i + 1))
            )
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    assert m.deadlock_pulses == 3


@pytest.mark.asyncio
async def test_deadlock_zero_when_accepted_proposal_is_latest(db_engine):
    """If no pulses exist after the last acceptance, deadlock = 0."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        u = uuid.uuid4()
        await _add_member(db, cid, u)
        for _ in range(2):
            await _add_pulse(db, cid)
        await _add_proposal(db, cid, u, ProposalStatus.ACCEPTED, age=1)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    # No pulses created AFTER the proposal → deadlock = 0
    assert m.deadlock_pulses == 0


@pytest.mark.asyncio
async def test_deadlock_anchors_on_decided_at_not_created_at(db_engine):
    """Repro for the deadlock-pulses anchoring bug: when a proposal was
    filed long ago but decided recently, deadlock must count from
    DECISION time. The buggy `max(Proposal.created_at)` would anchor on
    the file timestamp, counting every intervening pulse — including
    those that came BEFORE the decision — as deadlock.

    Setup:
        T+0  : Pulse A (DONE)
        T+0.5: Proposal P filed (created_at = T+0.5)
        T+1  : Pulse B (DONE)
        T+2  : Pulse C (DONE) — proposal P decided here (decided_at = T+2)
        T+3  : Pulse D (DONE)

    Correct deadlock: 1 (only Pulse D is after the decision).
    Bug: 3 (Pulses B/C/D all happened after the proposal was *filed*).
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        u = uuid.uuid4()
        await _add_member(db, cid, u)
        base = datetime.now(timezone.utc)

        # Pulse A at T+0
        pa = await _add_pulse(db, cid)
        # Proposal P at T+0.5, decided at T+2
        pid = await _add_proposal(db, cid, u, ProposalStatus.ACCEPTED, age=1)
        # Pulses B, C, D at T+1, T+2, T+3
        pb = await _add_pulse(db, cid)
        pc = await _add_pulse(db, cid)
        pd = await _add_pulse(db, cid)
        await db.flush()

        # Fix timestamps deterministically.
        for pulse_id, dt in (
            (pa, base),
            (pb, base + timedelta(seconds=1)),
            (pc, base + timedelta(seconds=2)),
            (pd, base + timedelta(seconds=3)),
        ):
            await db.execute(
                update(Pulse).where(Pulse.id == pulse_id).values(created_at=dt)
            )
        # Critical: proposal was filed BEFORE pulse B, decided at pulse C time.
        await db.execute(
            update(Proposal)
            .where(Proposal.id == pid)
            .values(
                created_at=base + timedelta(milliseconds=500),
                decided_at=base + timedelta(seconds=2),
            )
        )
        await db.commit()

    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    # Only pulse D is strictly after the decision time.
    assert m.deadlock_pulses == 1, (
        f"Expected 1 deadlock pulse (only Pulse D after decision), got "
        f"{m.deadlock_pulses}. The bug anchors on created_at instead of "
        f"decided_at — counting Pulses B/C/D all of which happened after "
        f"the proposal was *filed*."
    )


@pytest.mark.asyncio
async def test_deadlock_equals_total_when_nothing_accepted(db_engine):
    """No accepted proposals ever → deadlock = total_pulses."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        u = uuid.uuid4()
        await _add_member(db, cid, u)
        for _ in range(4):
            await _add_pulse(db, cid)
        await _add_proposal(db, cid, u, ProposalStatus.REJECTED, age=2)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    assert m.deadlock_pulses == 4


@pytest.mark.asyncio
async def test_ttq_percentiles_computed_correctly(db_engine):
    """Ages [1,1,2,3,5,8,13] at acceptance → p50=3, p95=13."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        cid = await _seed_community(db)
        u = uuid.uuid4()
        await _add_member(db, cid, u)
        for age in [1, 1, 2, 3, 5, 8, 13]:
            await _add_proposal(db, cid, u, ProposalStatus.ACCEPTED, age=age)
        await db.commit()
    async with sf() as db:
        m = await MetricsService(db).compute(cid)
    assert m.ttq_p50 == 3.0
    assert m.ttq_p95 == 13.0
