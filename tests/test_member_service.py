"""Direct service-level tests for MemberService — things that are hard
to provoke via the HTTP layer but still reachable in production.
"""
"""Direct service-level tests for MemberService — things that are hard
to provoke via the HTTP layer but still reachable in production.
"""
import uuid

import pytest
from sqlalchemy import select

from kbz.models.community import Community
from kbz.schemas.community import CommunityCreate
from kbz.schemas.user import UserCreate
from kbz.services.community_service import CommunityService
from kbz.services.member_service import MemberService
from kbz.services.user_service import UserService


async def _mk_user(db, name: str):
    return await UserService(db).create(UserCreate(user_name=name, password="test123"))


async def _mk_community(db, founder_id: uuid.UUID):
    return await CommunityService(db).create(
        CommunityCreate(name=f"C-{uuid.uuid4().hex[:6]}", founder_user_id=founder_id)
    )


@pytest.mark.asyncio
async def test_throw_out_noop_when_target_not_active_preserves_count(db):
    """Regression: throw_out used to unconditionally decrement
    Community.member_count regardless of whether the target was
    actually an ACTIVE member. Accepted ThrowOut proposals don't
    validate the TARGET (only the proposer), so a stale/retargeted
    ThrowOut could drive member_count below zero."""
    founder = await _mk_user(db, "founder")
    outsider = await _mk_user(db, "outsider")
    community = await _mk_community(db, founder.id)

    starting_count = (
        await db.execute(select(Community).where(Community.id == community.id))
    ).scalar_one().member_count
    assert starting_count == 1  # just the founder

    await MemberService(db).throw_out(community.id, outsider.id)

    refreshed = (
        await db.execute(select(Community).where(Community.id == community.id))
    ).scalar_one()
    assert refreshed.member_count == starting_count, (
        "member_count must not drift when throw_out targets a non-member"
    )


@pytest.mark.asyncio
async def test_throw_out_noop_on_already_thrown_out_member(db):
    """Calling throw_out twice must not double-decrement member_count."""
    founder = await _mk_user(db, "founder2")
    joiner = await _mk_user(db, "joiner")
    community = await _mk_community(db, founder.id)

    svc = MemberService(db)
    await svc.create(community.id, joiner.id)
    await db.flush()

    count_after_join = (
        await db.execute(select(Community).where(Community.id == community.id))
    ).scalar_one().member_count
    assert count_after_join == 2

    await svc.throw_out(community.id, joiner.id)
    await svc.throw_out(community.id, joiner.id)  # second call must no-op

    final = (
        await db.execute(select(Community).where(Community.id == community.id))
    ).scalar_one()
    assert final.member_count == 1


@pytest.mark.asyncio
async def test_throw_out_deactivates_bot_profiles(db):
    """A thrown-out member's BotProfile must be set inactive — pre-fix
    the bot kept voting, supporting, and commenting on its owner's
    behalf even after the community had explicitly removed them,
    defeating the entire point of ThrowOut. Cascade also covers child
    actions where a parent ThrowOut removes the user."""
    from kbz.models.bot_profile import BotProfile
    founder = await _mk_user(db, "tobot-founder")
    joiner = await _mk_user(db, "tobot-joiner")
    community = await _mk_community(db, founder.id)
    svc = MemberService(db)
    await svc.create(community.id, joiner.id)
    await db.flush()

    # Seed an ACTIVE bot for the joiner in this community.
    bot = BotProfile(
        user_id=joiner.id, community_id=community.id, active=True,
        display_name="Joiner Bot",
    )
    db.add(bot)
    await db.flush()

    await svc.throw_out(community.id, joiner.id)
    await db.flush()

    refreshed = (
        await db.execute(select(BotProfile).where(BotProfile.id == bot.id))
    ).scalar_one()
    assert refreshed.active is False, (
        "BotProfile must be deactivated when its owner is thrown out — "
        "otherwise the bot keeps acting after the human is removed."
    )


@pytest.mark.asyncio
async def test_create_duplicate_member_is_idempotent_no_double_count(db):
    """Regression: two Membership accepts for the same applicant in one
    pulse used to make the 2nd INSERT raise IntegrityError, which
    propagated out of execute_pulse and aborted the ENTIRE pulse. The
    create() savepoint now absorbs the duplicate as a no-op and does NOT
    double-bump member_count."""
    founder = await _mk_user(db, "dup-founder")
    joiner = await _mk_user(db, "dup-joiner")
    community = await _mk_community(db, founder.id)
    svc = MemberService(db)

    m1 = await svc.create(community.id, joiner.id)
    await db.flush()
    count_after_first = (
        await db.execute(select(Community).where(Community.id == community.id))
    ).scalar_one().member_count

    # Second create for the same (community, user) must NOT raise and must
    # NOT increment the count again.
    m2 = await svc.create(community.id, joiner.id)
    await db.flush()
    count_after_second = (
        await db.execute(select(Community).where(Community.id == community.id))
    ).scalar_one().member_count

    assert m1.user_id == m2.user_id
    assert count_after_second == count_after_first, (
        "duplicate member create must not double-count"
    )
