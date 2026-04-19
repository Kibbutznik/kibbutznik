"""Pure-logic tests for bot_runner — avoid the LLM + HTTP loopback.

We cover:
  - `_profile_to_persona` — the config→Persona mapping that drives the
    whole prompt. Regressions here silently change every bot's behavior.
  - `_fetch_due_profiles` — the cooldown filter. A bug here would either
    thrash the LLM (too many turns) or let bots stall (too few).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from agents.bot_runner import BotRunner, _profile_to_persona
from kbz.models.bot_profile import BotProfile
from kbz.models.user import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_user() -> User:
    return User(
        id=uuid.uuid4(),
        user_name="alice",
        password_hash="",
        about="",
        wallet_address="",
        email="alice@example.com",
        is_human=True,
    )


def _make_profile(
    *, user_id=None, community_id=None, orientation="pragmatist",
    initiative=5, agreeableness=5, goals="", boundaries="",
    display_name=None, active=True, approval_mode="autonomous",
    turn_interval_seconds=300, last_turn_at=None,
):
    return BotProfile(
        id=uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        community_id=community_id or uuid.uuid4(),
        active=active,
        display_name=display_name,
        orientation=orientation,
        initiative=initiative,
        agreeableness=agreeableness,
        goals=goals,
        boundaries=boundaries,
        approval_mode=approval_mode,
        turn_interval_seconds=turn_interval_seconds,
        last_turn_at=last_turn_at,
        created_at=_now(),
        updated_at=_now(),
    )


# ── _profile_to_persona ─────────────────────────────────────────────

def test_persona_name_falls_back_to_username_when_no_display_name():
    user = _make_user()
    profile = _make_profile()
    p = _profile_to_persona(profile, user)
    assert p.name == "alice-bot"


def test_persona_uses_explicit_display_name_when_set():
    user = _make_user()
    profile = _make_profile(display_name="ProductivityCoach")
    p = _profile_to_persona(profile, user)
    assert p.name == "ProductivityCoach"


def test_persona_initiative_and_cooperation_come_from_sliders():
    """initiative=9 + agreeableness=2 must land in the 0..1 range the
    existing Persona.traits object expects."""
    user = _make_user()
    profile = _make_profile(initiative=9, agreeableness=2)
    p = _profile_to_persona(profile, user)
    assert p.traits.initiative == pytest.approx(0.9)
    assert p.traits.cooperation == pytest.approx(0.2)


def test_persona_orientation_maps_to_role_and_trait_overrides():
    user = _make_user()
    # devils_advocate orientation has trait overrides (confrontation 0.85)
    profile = _make_profile(orientation="devils_advocate", initiative=5, agreeableness=5)
    p = _profile_to_persona(profile, user)
    assert "devil" in p.role.lower()
    assert p.traits.confrontation == pytest.approx(0.85)
    # initiative slider override: 5 → 0.5
    assert p.traits.initiative == pytest.approx(0.5)


def test_persona_goals_land_in_background():
    user = _make_user()
    profile = _make_profile(goals="Ship the onboarding handbook by Friday.")
    p = _profile_to_persona(profile, user)
    assert "Ship the onboarding handbook" in p.background


def test_persona_boundaries_land_in_decision_style_prefixed():
    user = _make_user()
    profile = _make_profile(boundaries="Never propose ThrowOut.")
    p = _profile_to_persona(profile, user)
    # Must be flagged as HARD BOUNDARIES so the LLM treats it as a rule
    assert "HARD BOUNDARIES" in p.decision_style
    assert "Never propose ThrowOut." in p.decision_style


def test_persona_empty_goals_get_a_sensible_default():
    user = _make_user()
    profile = _make_profile(goals="")
    p = _profile_to_persona(profile, user)
    assert p.background  # not empty
    assert "delegated proxy" in p.background.lower()


def test_persona_unknown_orientation_falls_back_to_pragmatist():
    """Should NEVER raise — profiles validated at API boundary, but
    _profile_to_persona has a conservative fallback too."""
    user = _make_user()
    profile = _make_profile(orientation="astrologer")
    p = _profile_to_persona(profile, user)
    assert p.role == "pragmatist"


# ── _fetch_due_profiles ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_due_profiles_includes_never_run(db_engine):
    """A newly-activated bot (last_turn_at=None) is always due."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = _make_user()
        db.add(user)
        await db.flush()
        profile = _make_profile(user_id=user.id, last_turn_at=None)
        db.add(profile)
        await db.commit()

    runner = BotRunner(session_factory=sf, engine=None)
    async with sf() as db:
        due = await runner._fetch_due_profiles(db, _now())
    # Match by id, since SQLAlchemy returns fresh objects
    ids = [p.id for p, _ in due]
    assert profile.id in ids


@pytest.mark.asyncio
async def test_due_profiles_excludes_recently_turned(db_engine):
    """last_turn_at was just now + interval 300s → not due yet."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = _make_user()
        db.add(user)
        await db.flush()
        profile = _make_profile(
            user_id=user.id,
            turn_interval_seconds=300,
            last_turn_at=_now() - timedelta(seconds=60),  # 60s ago < 300s
        )
        db.add(profile)
        await db.commit()

    runner = BotRunner(session_factory=sf, engine=None)
    async with sf() as db:
        due = await runner._fetch_due_profiles(db, _now())
    assert not any(p.id == profile.id for p, _ in due)


@pytest.mark.asyncio
async def test_due_profiles_includes_cooldown_elapsed(db_engine):
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = _make_user()
        db.add(user)
        await db.flush()
        profile = _make_profile(
            user_id=user.id,
            turn_interval_seconds=60,
            last_turn_at=_now() - timedelta(seconds=120),  # >>60s
        )
        db.add(profile)
        await db.commit()

    runner = BotRunner(session_factory=sf, engine=None)
    async with sf() as db:
        due = await runner._fetch_due_profiles(db, _now())
    assert any(p.id == profile.id for p, _ in due)


@pytest.mark.asyncio
async def test_due_profiles_skips_inactive(db_engine):
    """active=False → never due, no matter the cooldown."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = _make_user()
        db.add(user)
        await db.flush()
        profile = _make_profile(user_id=user.id, active=False, last_turn_at=None)
        db.add(profile)
        await db.commit()

    runner = BotRunner(session_factory=sf, engine=None)
    async with sf() as db:
        due = await runner._fetch_due_profiles(db, _now())
    assert not any(p.id == profile.id for p, _ in due)


@pytest.mark.asyncio
async def test_due_profiles_skips_review_mode(db_engine):
    """approval_mode=review not yet wired for autonomous execution."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = _make_user()
        db.add(user)
        await db.flush()
        profile = _make_profile(
            user_id=user.id, approval_mode="review", last_turn_at=None,
        )
        db.add(profile)
        await db.commit()

    runner = BotRunner(session_factory=sf, engine=None)
    async with sf() as db:
        due = await runner._fetch_due_profiles(db, _now())
    assert not any(p.id == profile.id for p, _ in due)
