"""BotRunner — background task that gives human-delegated AI bots their
turn in the kibbutzim their owners are members of.

Architecture
------------
One BotRunner per process, started in the FastAPI lifespan alongside the
main simulation's orchestrator. Every `poll_interval` seconds it:

    1. Pulls all `BotProfile` rows with `active=true` from the DB.
    2. Filters to those whose `last_turn_at` is older than their own
       `turn_interval_seconds` (or NULL — new bots).
    3. For each, builds a `Persona` from the profile fields, spins up a
       transient `Agent` authenticated as the profile's user, and calls
       `think_and_act()` — exactly the same code path the sim's agents
       use. Everything writes through the normal HTTP API loopback
       (`KBZClient` at `http://localhost:8000`) so the bot's actions
       look identical to human actions from the system's point of view.
    4. Updates `last_turn_at` on the profile whether or not the turn
       produced any action (so we don't thrash).

Errors per bot are swallowed + logged; one broken profile doesn't
stop the runner. The main sim's orchestrator and the BotRunner share
nothing except the LLM backend + HTTP endpoint — they're independent.

`approval_mode="review"` is recognized in the profile schema but the
runner currently skips bots in review mode (no human-in-the-loop UI
yet). Set `active=false` instead if you don't want the bot to act.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.agent import Agent
from agents.api_client import KBZClient
from agents.decision_engine import DecisionEngine
from agents.memory import MemoryStore
from agents.persona import Persona, Traits
from agents.tkg_client import TKGClient
from kbz.models.bot_profile import BotProfile
from kbz.models.user import User

logger = logging.getLogger(__name__)


# Map the simple orientation string a human picks on the form to a
# Persona `role` string and a trait-override dict. Keeps the profile
# schema human-friendly while still producing persona prompts that the
# existing DecisionEngine can work with.
_ORIENTATION_PROFILES: dict[str, tuple[str, dict[str, float]]] = {
    "producer":         ("hands-on builder", {"initiative": 0.8, "patience": 0.6, "confrontation": 0.4}),
    "consensus":        ("consensus-builder", {"cooperation": 0.85, "confrontation": 0.25, "social_energy": 0.7}),
    "devils_advocate":  ("devil's advocate", {"confrontation": 0.85, "openness": 0.7, "cooperation": 0.45}),
    "idealist":         ("values-first idealist", {"openness": 0.85, "loyalty": 0.5, "initiative": 0.7}),
    "pragmatist":       ("pragmatist", {"openness": 0.55, "cooperation": 0.6, "confrontation": 0.5}),
    "diplomat":         ("diplomat", {"cooperation": 0.75, "social_energy": 0.8, "confrontation": 0.35}),
}


def _profile_to_persona(profile: BotProfile, user: User) -> Persona:
    """Translate a BotProfile + its owning User into the Persona shape
    the existing DecisionEngine consumes. Free-text `goals` and
    `boundaries` land in the `background` + `decision_style` fields so
    they flow into the LLM prompt verbatim."""
    role, overrides = _ORIENTATION_PROFILES.get(
        profile.orientation,
        _ORIENTATION_PROFILES["pragmatist"],
    )
    traits = Traits()
    for k, v in overrides.items():
        setattr(traits, k, v)
    # initiative/agreeableness sliders ultimately override preset defaults
    traits.initiative = profile.initiative / 10.0
    traits.cooperation = profile.agreeableness / 10.0

    name = profile.display_name or f"{user.user_name}-bot"
    bg = (profile.goals or "").strip() or (
        "A delegated proxy acting on behalf of a human member. No custom "
        "goals provided — defers to community priorities."
    )
    boundaries = (profile.boundaries or "").strip()
    decision_style = (
        "Act as a proxy for the human — keep the community moving. "
        "Favor short, punchy proposals and comments."
    )
    if boundaries:
        decision_style += f"\n\nHARD BOUNDARIES (never violate):\n{boundaries}"

    comm_style = (
        "Brief and practical. Signs comments and chat as \""
        f"{name}\" when helpful. Avoid long-winded editorial prose."
    )

    return Persona(
        name=name,
        role=role,
        traits=traits,
        background=bg,
        decision_style=decision_style,
        communication_style=comm_style,
    )


class BotRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: DecisionEngine,
        api_base_url: str = "http://localhost:8000",
        poll_interval_seconds: float = 30.0,
    ):
        self._sf = session_factory
        self._engine = engine
        self._api_base_url = api_base_url
        self._poll_interval = poll_interval_seconds
        self._task: asyncio.Task | None = None
        self._stopping = False
        # One shared MemoryStore + TKGClient for all bot turns (they just
        # open short-lived HTTP sessions internally; reuse keeps the
        # connection pool warm).
        self._memory = MemoryStore(api_base_url)
        self._tkg = TKGClient(api_base_url)

    # ---- lifecycle -------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="bot-runner")
        logger.info("[BotRunner] started (poll every %ss)", self._poll_interval)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---- main loop -------------------------------------------------
    async def _run(self) -> None:
        try:
            while not self._stopping:
                try:
                    await self._tick()
                except Exception:
                    logger.exception("[BotRunner] tick failed")
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise

    async def _tick(self) -> None:
        """Scan for due bots, run a turn for each."""
        now = datetime.now(timezone.utc)
        async with self._sf() as db:
            due = await self._fetch_due_profiles(db, now)
        if not due:
            return
        logger.info("[BotRunner] %d bot(s) due for a turn", len(due))
        for profile, user in due:
            try:
                await self._run_one_turn(profile, user)
            except Exception:
                logger.exception(
                    "[BotRunner] turn failed for user=%s community=%s",
                    profile.user_id, profile.community_id,
                )
            # Update last_turn_at regardless — prevents thrashing on
            # bots that keep failing.
            async with self._sf() as db:
                await db.execute(
                    BotProfile.__table__.update()
                    .where(BotProfile.id == profile.id)
                    .values(last_turn_at=datetime.now(timezone.utc))
                )
                await db.commit()

    async def _fetch_due_profiles(
        self, db: AsyncSession, now: datetime,
    ) -> list[tuple[BotProfile, User]]:
        # A profile is "due" if it's active + autonomous + either has
        # never run OR the cooldown has elapsed.
        stmt = (
            select(BotProfile, User)
            .join(User, User.id == BotProfile.user_id)
            .where(
                BotProfile.active.is_(True),
                BotProfile.approval_mode == "autonomous",
            )
        )
        rows = (await db.execute(stmt)).all()
        due = []
        for profile, user in rows:
            if profile.last_turn_at is None:
                due.append((profile, user))
                continue
            interval = timedelta(seconds=profile.turn_interval_seconds)
            if now - profile.last_turn_at >= interval:
                due.append((profile, user))
        return due

    async def _run_one_turn(self, profile: BotProfile, user: User) -> None:
        persona = _profile_to_persona(profile, user)
        # Build the agent fresh each turn — per-turn in-memory state
        # (supported_proposals, commented_proposals) is fine to reset;
        # the DB is the source of truth for what's already been done.
        client = KBZClient(self._api_base_url)
        try:
            agent = Agent(
                persona=persona,
                client=client,
                engine=self._engine,
                user_id=str(user.id),
                user_name=user.user_name,
                memory_store=self._memory,
                tkg_client=self._tkg,
            )
            agent.community_id = str(profile.community_id)
            logs = await agent.think_and_act()
            n_ok = sum(1 for l in logs if l.success)
            logger.info(
                "[BotRunner] turn done for %s in %s: %d/%d actions OK",
                persona.name, str(profile.community_id)[:8], n_ok, len(logs),
            )
        finally:
            await client.close()
