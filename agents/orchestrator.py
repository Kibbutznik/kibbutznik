"""
Orchestrator — runs multiple AI agents in a KBZ community.

Sets up a community, registers agents, and runs them through
governance cycles. This is the "simulation engine".
"""
import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone

from agents.agent import Agent
from agents.api_client import KBZClient
from agents.decision_engine import DecisionEngine
from agents.memory import MemoryStore
from agents.persona import Persona, load_all_personas, generate_persona
from agents.tkg_client import TKGClient
from kbz.services.event_bus import event_bus

logger = logging.getLogger(__name__)

# Pool of names for newcomers who want to join
NEWCOMER_NAMES = [
    "Alex", "Sam", "Jordan", "Morgan", "Casey", "Riley",
    "Avery", "Quinn", "Blake", "Drew", "Jamie", "Kai",
    "Skyler", "Reese", "Finley", "Rowan", "Emery", "Sage",
    "River", "Hayden", "Phoenix", "Dakota", "Remi", "Shiloh",
    "Lennon", "Lyric", "Nova", "Zion", "Cruz", "Indigo",
]


@dataclass
class SimulationEvent:
    """A recorded event from the simulation for the viewer."""
    timestamp: datetime
    agent_name: str
    action_type: str
    details: str
    reason: str
    success: bool
    eagerness: int = 5
    eager_front: str = "observe"
    community_id: str | None = None  # which community this event belongs to
    ref_id: str | None = None        # full ID of referenced entity (proposal, etc.) for viewer linking


class Orchestrator:
    """
    Manages a community simulation with multiple AI agents.

    Lifecycle:
    1. setup() — create community, register agents, add initial members
    2. run(rounds) — run agents for N rounds
    3. Each round: shuffle agent order → each agent observes+acts
    """

    def __init__(
        self,
        community_name: str = "AI Kibbutz",
        mission: str | None = None,
        api_url: str = "http://localhost:8000",
        llm_backend: str = "anthropic",
        llm_model: str = "claude-haiku-4-5-20251001",
        personas: list[Persona] | None = None,
        max_idle_rounds: int = 3,
        agents_per_round_fraction: float = 0.75,
        # Ollama-specific options (ignored for Anthropic)
        ollama_timeout: float = 300.0,
        ollama_num_ctx: int = 8192,
        ollama_temperature: float = 0.7,
        ollama_num_predict: int = 2048,
        max_retries: int = 3,
        ollama_think: bool = False,
    ):
        self.community_name = community_name
        self.mission = mission
        self.api_url = api_url
        self.client = KBZClient(api_url)
        self.engine = DecisionEngine(
            backend=llm_backend,
            model=llm_model,
            ollama_timeout=ollama_timeout,
            ollama_num_ctx=ollama_num_ctx,
            ollama_temperature=ollama_temperature,
            ollama_num_predict=ollama_num_predict,
            max_retries=max_retries,
            ollama_think=ollama_think,
        )
        self.personas = personas or load_all_personas()
        self.agents: list[Agent] = []
        self.community_id: str | None = None
        self.founder_id: str | None = None
        self.events: list[SimulationEvent] = []
        self._round = 0
        self._paused = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # starts unpaused
        # ── API-credit safety: auto-pause every N events ──────────────
        # After N events the sim pauses itself so unattended runs don't
        # burn unbounded OpenRouter credit. Set to 0 to disable. When
        # the viewer resumes, the counter resets and the sim runs for
        # another N events before the next auto-pause.
        self._auto_pause_every: int = 500
        self._events_since_resume: int = 0
        self.max_idle_rounds = max_idle_rounds
        self.agents_per_round_fraction = agents_per_round_fraction
        self.newcomer_users: list[dict] = []  # users who applied but aren't members yet
        self._newcomer_name_idx: int = 0   # monotonic counter — never decreases even when newcomers are promoted
        self._newcomer_prob: float = 0.8   # ~80% chance per round — frequent membership activity
        # Cached community data refreshed during rounds, used by get_status()
        # to avoid self-referencing HTTP calls that cause httpx.ReadError.
        self._cached_community: dict | None = None
        self._cached_members: list[dict] | None = None
        self._cached_variables: dict | None = None
        # Pulse deadlock detection: count rounds since a pulse last fired
        self._rounds_since_pulse: int = 0
        # After this many stuck rounds, nudge agents to support the pulse
        self._pulse_deadlock_threshold: int = 5
        # "Big Brother" observer user — created during setup, used to post
        # admin messages into the community chat from the viewer.
        self._bb_user_id: str | None = None
        # Agent memory system
        self.memory_store = MemoryStore(api_url)
        self.tkg_client = TKGClient(api_url)
        self._reflection_interval: int = 5  # generate reflections every N rounds

    async def post_chat(self, message: str, community_id: str | None = None) -> dict:
        """Post a message to a community chat as Big Brother.

        If *community_id* is None, the message goes to the root community.
        """
        if not self._bb_user_id:
            raise RuntimeError("Big Brother user not initialised — call setup() first")
        target = community_id or self.community_id
        return await self.client.add_comment(
            entity_type="community",
            entity_id=target,
            user_id=self._bb_user_id,
            comment_text=message,
        )

    async def setup(self) -> None:
        """Create the community and register all agents."""
        logger.info(f"Setting up simulation: {self.community_name}")

        # Create agents from personas
        for persona in self.personas:
            agent = Agent(
                persona=persona,
                client=self.client,
                engine=self.engine,
                memory_store=self.memory_store,
                tkg_client=self.tkg_client,
            )
            await agent.register()
            self.agents.append(agent)

        # First agent founds the community
        founder = self.agents[0]
        self.founder_id = founder.user_id
        community = await self.client.create_community(
            self.community_name,
            founder.user_id,
            initial_artifact_mission=self.mission,
        )
        self.community_id = community["id"]
        founder.community_id = self.community_id

        logger.info(f"Community created: {self.community_name} ({self.community_id})")
        logger.info(f"Founder: {founder.persona.name}")

        # Create the Big Brother observer account used by the viewer operator
        # to inject messages into the community chat.
        bb = await self.client.create_user(
            user_name="Big Brother",
            password="bb-observer",
            about="Viewer operator — can send messages to guide the community.",
        )
        self._bb_user_id = str(bb.get("user_id") or bb.get("id"))
        logger.info(f"Big Brother user created: {self._bb_user_id}")

        # Add remaining agents as members through governance
        # For bootstrap, we add them directly via membership proposals that the founder approves
        for agent in self.agents[1:]:
            await self._bootstrap_member(agent)

        logger.info(f"All {len(self.agents)} agents registered and added to community")

        # Seed a handful of random positive-affinity pairs so the closeness
        # graph isn't flat at round 0. The EMA dynamics in ClosenessService
        # then evolve these seeds based on how the pairs actually vote.
        try:
            import uuid as _uuid
            from kbz.database import async_session as _session
            from kbz.services.closeness_service import (
                ClosenessService as _ClosenessService,
            )

            member_uuids = [_uuid.UUID(a.user_id) for a in self.agents]
            async with _session() as db:
                svc = _ClosenessService(db)
                # Hygiene: drop leftover rows from dead runs before seeding.
                purged = await svc.purge_stale_rows(older_than_hours=12)
                seeded = await svc.seed_initial_pairs(member_uuids)
                await db.commit()
                logger.info(
                    "Closeness: purged %d stale rows, seeded %d initial pairs",
                    purged, seeded,
                )
        except Exception as e:
            logger.warning("Closeness seed/purge skipped: %s", e)

        # Prime the status cache so /simulation/status works immediately.
        await self.refresh_status_cache()

    async def _bootstrap_member(self, agent: Agent) -> None:
        """Add an agent to the community during setup (fast-track membership)."""
        # Create membership proposal
        proposal = await self.client.create_proposal(
            community_id=self.community_id,
            user_id=agent.user_id,
            proposal_type="Membership",
            proposal_text=f"{agent.persona.name} wants to join the community",
            val_uuid=agent.user_id,
            pitch=f"{agent.persona.name} wants to join — bootstrap membership so the simulation has enough founding members to make decisions.",
        )
        await self.client.submit_proposal(proposal["id"])

        # All existing members support it
        for existing in self.agents:
            if existing.community_id == self.community_id:
                try:
                    await self.client.support_proposal(proposal["id"], existing.user_id)
                except Exception:
                    pass

        # Trigger pulse to move OutThere → OnTheAir
        for existing in self.agents:
            if existing.community_id == self.community_id:
                try:
                    result = await self.client.support_pulse(self.community_id, existing.user_id)
                    if result.get("pulse_triggered"):
                        break
                except Exception:
                    pass

        # Trigger another pulse to accept the proposal
        for existing in self.agents:
            if existing.community_id == self.community_id:
                try:
                    result = await self.client.support_pulse(self.community_id, existing.user_id)
                    if result.get("pulse_triggered"):
                        break
                except Exception:
                    pass

        agent.community_id = self.community_id
        logger.info(f"  Added {agent.persona.name} to community")

    def _select_agents_for_round(self) -> list[Agent]:
        """
        Select agents to act this round using eagerness-weighted scheduling
        with starvation prevention.

        - Agents idle > max_idle_rounds get a guaranteed slot.
        - Remaining slots filled by eagerness-weighted random draw (no replacement).
        - Acting agents sorted by eagerness descending (most eager acts first).
        """
        n_slots = max(1, round(len(self.agents) * self.agents_per_round_fraction))

        starving = [a for a in self.agents if a.rounds_since_acted > self.max_idle_rounds]
        candidates = [a for a in self.agents if a.rounds_since_acted <= self.max_idle_rounds]

        selected_ids: set[int] = {id(a) for a in starving}

        remaining = max(0, n_slots - len(starving))
        pool = list(candidates)
        weights = [float(a.eagerness) for a in pool]

        for _ in range(min(remaining, len(pool))):
            total = sum(weights)
            if not total:
                break
            r = random.random() * total
            cumulative = 0.0
            for i, w in enumerate(weights):
                cumulative += w
                if r <= cumulative:
                    selected_ids.add(id(pool[i]))
                    pool.pop(i)
                    weights.pop(i)
                    break

        acting = [a for a in self.agents if id(a) in selected_ids]
        acting.sort(key=lambda a: a.eagerness, reverse=True)
        return acting

    async def run_round(self) -> list[SimulationEvent]:
        """Run one round: selected agents observe and act (eagerness-weighted)."""
        self._round += 1
        round_events = []

        # Refresh cached community data for the viewer's /simulation/status
        # endpoint so it doesn't need to make self-referencing HTTP calls.
        await self.refresh_status_cache()

        acting_agents = self._select_agents_for_round()
        acting_ids = {id(a) for a in acting_agents}

        logger.info(f"\n{'='*60}")
        logger.info(
            f"Round {self._round} — {len(acting_agents)}/{len(self.agents)} agents acting: "
            f"{[f'{a.persona.name}(e={a.eagerness})' for a in acting_agents]}"
        )
        logger.info(f"{'='*60}")

        await event_bus.emit(
            "round.start",
            community_id=self.community_id,
            round=self._round,
        )

        for agent in acting_agents:
            agent.rounds_since_pulse = self._rounds_since_pulse
            agent.current_round = self._round
            try:
                agent_logs = await agent.think_and_act()
                # think_and_act resets rounds_since_acted to 0 internally
                for log in agent_logs:
                    event = SimulationEvent(
                        timestamp=log.timestamp,
                        agent_name=agent.persona.name,
                        action_type=log.action_type,
                        details=log.details,
                        reason=log.reason,
                        success=log.success,
                        eagerness=log.eagerness,
                        eager_front=log.eager_front,
                        community_id=self.community_id,
                        ref_id=log.ref_id,
                    )
                    round_events.append(event)
                    self.events.append(event)
                    await event_bus.emit(
                        "agent.action",
                        community_id=self.community_id,
                        agent_name=agent.persona.name,
                        action_type=log.action_type,
                        details=log.details,
                        reason=log.reason,
                        success=log.success,
                        round=self._round,
                        eagerness=log.eagerness,
                        eager_front=log.eager_front,
                        ref_id=log.ref_id,
                    )
            except Exception as e:
                logger.error(f"[{agent.persona.name}] Error in round {self._round}: {e}")
                agent.rounds_since_acted = 0  # still counts as having had a turn
                event = SimulationEvent(
                    timestamp=datetime.now(timezone.utc),
                    agent_name=agent.persona.name,
                    action_type="error",
                    details=str(e),
                    reason="",
                    success=False,
                    eagerness=agent.eagerness,
                    eager_front=agent.eager_front,
                    community_id=self.community_id,
                )
                round_events.append(event)
                self.events.append(event)

        # Increment idle counter for agents that did NOT act this round
        for agent in self.agents:
            if id(agent) not in acting_ids:
                agent.rounds_since_acted += 1

        # --- Action sub-community participation ---
        # ALL agents who are members of action communities observe & act there
        # (not just those selected for the main round — more communities = more activity)
        try:
            actions = await self.client.get_actions(self.community_id)
            if actions:
                action_membership: dict[str, set[str]] = {}
                for action in actions:
                    aid = action["action_id"]
                    try:
                        members = await self.client.get_members(aid)
                        action_membership[aid] = {m["user_id"] for m in members}
                    except Exception:
                        action_membership[aid] = set()

                # Every agent that is a member of an action community gets to act there
                for agent in self.agents:
                    for aid, member_ids in action_membership.items():
                        if agent.user_id in member_ids:
                            original_cid = agent.community_id
                            try:
                                agent.community_id = aid
                                agent.rounds_since_pulse = self._rounds_since_pulse
                                agent.current_round = self._round
                                action_logs = await agent.think_and_act()
                                for log in action_logs:
                                    event = SimulationEvent(
                                        timestamp=log.timestamp,
                                        agent_name=agent.persona.name,
                                        action_type=log.action_type,
                                        details=log.details,
                                        reason=log.reason,
                                        success=log.success,
                                        eagerness=log.eagerness,
                                        eager_front=log.eager_front,
                                        community_id=aid,
                                        ref_id=log.ref_id,
                                    )
                                    round_events.append(event)
                                    self.events.append(event)
                                    await event_bus.emit(
                                        "agent.action",
                                        community_id=aid,
                                        agent_name=agent.persona.name,
                                        action_type=log.action_type,
                                        details=f"[action] {log.details}",
                                        reason=log.reason,
                                        success=log.success,
                                        round=self._round,
                                        eagerness=log.eagerness,
                                        eager_front=log.eager_front,
                                        ref_id=log.ref_id,
                                    )
                            except Exception as e:
                                logger.error(f"[{agent.persona.name}] Error in action {aid[:8]}: {e}")
                            finally:
                                agent.community_id = original_cid
        except Exception as e:
            logger.error(f"Error in action community round: {e}")

        await event_bus.emit(
            "round.end",
            community_id=self.community_id,
            round=self._round,
            event_count=len(round_events),
        )

        # --- Pulse deadlock detection & forced nudge ---
        # Check if a pulse fired this round (look for pulse.executed events in the bus)
        pulse_fired = any(
            ev.action_type in ("pulse_support", "support_pulse")
            and ev.success
            and "pulse fired" in ev.details.lower()
            for ev in round_events
        )
        # Simpler: check if any round_event mentions pulse being triggered
        pulse_triggered = any(
            "pulse" in ev.details.lower() and ("trigger" in ev.details.lower() or "fired" in ev.details.lower())
            for ev in round_events
        )
        if pulse_fired or pulse_triggered:
            self._rounds_since_pulse = 0
        else:
            self._rounds_since_pulse += 1

        if self._rounds_since_pulse >= self._pulse_deadlock_threshold:
            await self._break_pulse_deadlock()

        # --- Agent memory: reflections + pruning ---
        if self._round % self._reflection_interval == 0:
            try:
                await self._generate_reflections()
            except Exception as e:
                logger.error(f"Reflection generation failed: {e}")

        # Prune memories every round (lightweight operation)
        for agent in self.agents:
            if agent.user_id and agent.memory_store:
                try:
                    await agent.memory_store.prune(agent.user_id, self._round)
                except Exception:
                    pass

        # Prune old closed TKG edges every 10 rounds to keep the graph lean
        if self._round > 0 and self._round % 10 == 0 and self.tkg_client:
            try:
                deleted = await self.tkg_client.prune_closed_edges(
                    older_than_round=max(0, self._round - 100),
                )
                if deleted:
                    logger.info(f"[TKG] Pruned {deleted} closed edges")
            except Exception as e:
                logger.debug(f"[TKG] prune_closed_edges failed: {e}")

        return round_events

    async def _break_pulse_deadlock(self) -> None:
        """Force 2-3 random agents to support the pulse when it has been stuck too long."""
        if not self.agents or not self.community_id:
            return
        n_nudge = min(3, len(self.agents))
        nudge_agents = random.sample(self.agents, n_nudge)
        nudged = 0
        for agent in nudge_agents:
            if not agent.user_id:
                continue
            try:
                result = await self.client.support_pulse(self.community_id, agent.user_id)
                if result.get("pulse_triggered"):
                    self._rounds_since_pulse = 0
                    logger.info(
                        f"[Deadlock-Breaker] Pulse forced by {agent.persona.name} after "
                        f"{self._rounds_since_pulse + 1} stuck rounds → FIRED!"
                    )
                    nudged += 1
                    break
                nudged += 1
            except Exception as e:
                logger.debug(f"[Deadlock-Breaker] support_pulse failed for {agent.persona.name}: {e}")
        if nudged:
            logger.warning(
                f"[Deadlock-Breaker] Pulse stuck for {self._rounds_since_pulse} rounds — "
                f"nudged {nudged} agents to support pulse."
            )
        self._rounds_since_pulse = 0  # reset counter regardless

    async def _generate_reflections(self) -> None:
        """Generate LLM-powered reflections for all agents.

        Called every ``_reflection_interval`` rounds. Each agent gets a short
        prompt asking it to reflect on recent experiences, goals, and
        relationships. The resulting text is stored as a "reflection" memory.
        """
        logger.info(f"[Memory] Generating reflections for {len(self.agents)} agents (round {self._round})")
        for agent in self.agents:
            if not agent.user_id or not agent.memory_store:
                continue
            try:
                store = agent.memory_store
                episodes = await store.get_recent(agent.user_id, "episodic", limit=12)
                goals = await store.get_goals(agent.user_id)
                relationships = await store.get_relationships(agent.user_id, limit=8)

                if not episodes and not goals and not relationships:
                    continue  # nothing to reflect on yet

                ep_text = "\n".join(f"- {m['content']}" for m in episodes[:12]) or "None yet."
                goal_text = "\n".join(f"- {m['content']}" for m in goals[:5]) or "None yet."
                rel_text = "\n".join(
                    f"- {agent.users_cache.get(m.get('related_id', ''), m.get('related_id', '?')[:8])}: {m['content']}"
                    for m in relationships[:8]
                ) or "None yet."

                prompt = (
                    f"You are {agent.persona.name}, {agent.persona.role} in a KBZ community.\n\n"
                    f"Review your recent experiences and write a brief personal reflection "
                    f"(3-4 sentences) covering:\n"
                    f"1. What you've learned or accomplished recently\n"
                    f"2. What you should focus on next\n"
                    f"3. Any insights about your relationships with other community members\n\n"
                    f"Recent events:\n{ep_text}\n\n"
                    f"Current goals:\n{goal_text}\n\n"
                    f"Relationships:\n{rel_text}\n\n"
                    f"Write your reflection in first person, staying in character. "
                    f"Be specific and actionable — not vague. 3-4 sentences only."
                )

                if self.engine.backend == "anthropic":
                    reflection_text = await self.engine._call_anthropic(prompt)
                elif self.engine.backend == "ollama":
                    reflection_text = await self.engine._call_ollama(prompt)
                elif self.engine.backend == "openrouter":
                    reflection_text = await self.engine._call_openrouter(prompt)
                else:
                    continue

                # Clean up: remove any JSON formatting the LLM might add
                reflection_text = reflection_text.strip()
                if reflection_text.startswith('"') and reflection_text.endswith('"'):
                    reflection_text = reflection_text[1:-1]
                # Cap at 800 chars
                if len(reflection_text) > 800:
                    reflection_text = reflection_text[:800].rsplit(" ", 1)[0] + "..."

                await store.add(
                    user_id=agent.user_id,
                    memory_type="reflection",
                    content=reflection_text,
                    importance=0.9,
                    round_num=self._round,
                )
                logger.debug(f"[Memory] Reflection for {agent.persona.name}: {reflection_text[:80]}...")

            except Exception as e:
                logger.warning(f"[Memory] Reflection failed for {agent.persona.name}: {e}")

    def pause(self, *, reason: str = "manual") -> None:
        """Pause the simulation. Current round will finish, then pause before the next.

        `reason` is a free-form tag for telemetry/UI ("manual", "auto_api_budget", …).
        """
        self._paused = True
        self._pause_event.clear()
        logger.info(f"Simulation PAUSED (reason={reason})")

    def resume(self) -> None:
        """Resume the simulation. Also resets the auto-pause event counter."""
        self._paused = False
        self._events_since_resume = 0
        self._pause_event.set()
        logger.info("Simulation RESUMED (auto-pause counter reset)")

    @property
    def is_paused(self) -> bool:
        return self._paused

    def _current_llm_preset(self) -> str:
        """Return the preset key for the current engine, or 'custom'."""
        from agents.simulation_api import LLM_PRESETS
        for key, cfg in LLM_PRESETS.items():
            if (cfg["backend"] == self.engine.backend
                    and cfg["model"] == self.engine.model
                    and cfg.get("think", False) == self.engine.ollama_think):
                return key
        return "custom"

    def set_llm(self, backend: str, model: str, ollama_think: bool = False) -> None:
        """Swap the LLM backend/model mid-simulation. Takes effect on the next agent turn."""
        new_engine = DecisionEngine(
            backend=backend,
            model=model,
            ollama_timeout=self.engine.ollama_timeout,
            ollama_num_ctx=self.engine.ollama_num_ctx,
            ollama_temperature=self.engine.ollama_temperature,
            ollama_num_predict=self.engine.ollama_num_predict,
            max_retries=self.engine.max_retries,
            ollama_think=ollama_think,
        )
        self.engine = new_engine
        # Propagate the new engine to every agent immediately
        for agent in self.agents:
            agent.engine = new_engine
        logger.info("LLM switched to backend=%s model=%s", backend, model)

    async def _maybe_spawn_newcomer(self) -> None:
        """Randomly spawn a newcomer who submits a Membership proposal."""
        if random.random() > self._newcomer_prob:
            return
        idx = self._newcomer_name_idx
        if idx >= len(NEWCOMER_NAMES):
            return  # name pool exhausted

        name = NEWCOMER_NAMES[idx]
        # Skip if this name already has a pending membership proposal
        if any(n["name"] == name for n in self.newcomer_users):
            return
        user_name = f"{name.lower()}_applicant"
        try:
            user = await self.client.create_user(
                user_name=user_name,
                about="Newcomer applying to join the community",
            )
            user_id = user["id"]
            # Generate the newcomer's persona now so we can use their background
            # as a pitch in the membership proposal
            persona = generate_persona(name)
            self.newcomer_users.append({"id": user_id, "name": name, "persona": persona})
            self._newcomer_name_idx += 1  # advance permanently — never reuse a name

            # Build a meaningful pitch from the newcomer's persona. The short
            # proposal_text is the "what" (applicant name / short line) and
            # pitch carries the "why" (background + communication style).
            pitch = (
                f"{persona.background} "
                f"{persona.communication_style}"
            )

            proposal = await self.client.create_proposal(
                community_id=self.community_id,
                user_id=user_id,
                proposal_type="Membership",
                proposal_text=f"{name} wants to join the community",
                val_uuid=user_id,
                pitch=pitch,
            )
            await self.client.submit_proposal(proposal["id"])

            logger.info(
                f"[Newcomer] {name} ({user_name}) applied for membership "
                f"(proposal {proposal['id'][:8]})"
            )

            event = SimulationEvent(
                timestamp=datetime.now(timezone.utc),
                agent_name=name,
                action_type="create_proposal",
                details=f"Membership proposal: \"{name} wants to join the community\"",
                reason="New applicant — wants to become a community member",
                success=True,
                eagerness=9,
                eager_front="propose",
                community_id=self.community_id,
            )
            self.events.append(event)
            await event_bus.emit(
                "agent.action",
                community_id=self.community_id,
                agent_name=name,
                action_type="create_proposal",
                details=event.details,
                reason=event.reason,
                success=True,
                round=self._round,
                eagerness=9,
                eager_front="propose",
            )
        except Exception as e:
            logger.error(f"Newcomer spawn error ({name}): {e}")

    async def _check_newcomer_acceptance(self) -> None:
        """Promote accepted newcomers to full AI agents."""
        if not self.newcomer_users:
            return
        try:
            members = await self.client.get_members(self.community_id)
            member_ids = {m["user_id"] for m in members}
        except Exception as e:
            logger.error(f"Failed to fetch members for newcomer check: {e}")
            return

        promoted = []
        for newcomer in self.newcomer_users:
            if newcomer["id"] in member_ids:
                persona = newcomer.get("persona") or generate_persona(newcomer["name"])
                agent = Agent(
                    persona=persona,
                    client=self.client,
                    engine=self.engine,
                    user_id=newcomer["id"],
                    memory_store=self.memory_store,
                    tkg_client=self.tkg_client,
                )
                agent.community_id = self.community_id
                # Ensure the agent recognizes itself in community snapshots
                agent.users_cache[newcomer["id"]] = newcomer["name"]
                self.agents.append(agent)
                promoted.append(newcomer)

                logger.info(
                    f"[Newcomer] {newcomer['name']} accepted → now a full AI agent! "
                    f"(background: {persona.background[:60]}...)"
                )

                event = SimulationEvent(
                    timestamp=datetime.now(timezone.utc),
                    agent_name=newcomer["name"],
                    action_type="promoted",
                    details=f"{newcomer['name']} accepted as member → now an active AI agent",
                    reason="Membership proposal accepted",
                    success=True,
                    eagerness=7,
                    eager_front="observe",
                    community_id=self.community_id,
                )
                self.events.append(event)
                await event_bus.emit(
                    "agent.action",
                    community_id=self.community_id,
                    agent_name=newcomer["name"],
                    action_type="promoted",
                    details=event.details,
                    reason=event.reason,
                    success=True,
                    round=self._round,
                    eagerness=7,
                    eager_front="observe",
                )

        for p in promoted:
            self.newcomer_users.remove(p)

    # Maximum events kept in memory (older ones are discarded for long simulations)
    MAX_EVENTS = 5000

    def _trim_events(self) -> None:
        """Keep events list bounded for long-running simulations."""
        if len(self.events) > self.MAX_EVENTS:
            trimmed = len(self.events) - self.MAX_EVENTS
            self.events = self.events[-self.MAX_EVENTS:]
            logger.debug(f"Trimmed {trimmed} old events (keeping last {self.MAX_EVENTS})")

    async def run(self, rounds: int = 10, delay: float = 1.0) -> None:
        """Run the full simulation for N rounds.

        Args:
            rounds: Number of rounds to run. Use 0 for continuous (infinite) simulation.
            delay: Seconds to wait between rounds.
        """
        continuous = rounds == 0
        label = "continuous" if continuous else f"{rounds} rounds"
        logger.info(f"Starting simulation: {label}, {len(self.agents)} agents, backend={self.engine.backend}/{self.engine.model}")

        # Pre-flight health check for Ollama
        if self.engine.backend == "ollama":
            health = await self.engine.health_check()
            if not health.get("available"):
                logger.error(f"Ollama model '{self.engine.model}' not available! {health}")
                logger.error(f"Available models: {health.get('all_models', [])}")
                logger.error("Run: ollama pull <model> to download it first.")
                return
            logger.info(f"Ollama health check OK: model={self.engine.model}")

        round_num = 0
        while continuous or round_num < rounds:
            # Wait if paused
            await self._pause_event.wait()

            # Check if any pending newcomers got accepted → promote to full agents
            # (must run BEFORE run_round so new agents can participate immediately)
            await self._check_newcomer_acceptance()

            events = await self.run_round()

            # Maybe a newcomer applies this round
            await self._maybe_spawn_newcomer()

            # Print round summary
            for ev in events:
                logger.info(
                    f"  [{ev.agent_name}] {ev.action_type}: {ev.details} | {ev.reason[:80]}"
                )

            # Trim events to prevent unbounded memory growth
            self._trim_events()

            # ── Auto-pause after N events since last resume ────────────
            # Protects unattended runs from eating unbounded OpenRouter
            # credit. Checked after each round so we don't pause mid-round.
            if self._auto_pause_every > 0:
                self._events_since_resume += len(events)
                if self._events_since_resume >= self._auto_pause_every and not self._paused:
                    logger.warning(
                        f"[Auto-pause] {self._events_since_resume} events since last resume "
                        f"(threshold={self._auto_pause_every}) — pausing simulation to preserve API credit."
                    )
                    self.pause(reason="auto_api_budget")
                    try:
                        await event_bus.emit(
                            "simulation.auto_paused",
                            events_in_window=self._events_since_resume,
                            threshold=self._auto_pause_every,
                            round_num=self._round,
                        )
                    except Exception:
                        pass

            # Log LLM stats every 10 rounds for monitoring long simulations
            round_num += 1
            if round_num % 10 == 0:
                stats = self.engine.stats
                logger.info(
                    f"[LLM Stats] Round {round_num}: {stats['calls']} calls, "
                    f"avg {stats['avg_latency_s']}s, {stats['errors']} errors, "
                    f"{len(self.events)} events in memory"
                )

            if delay > 0:
                await asyncio.sleep(delay)

        logger.info(f"\nSimulation complete after {round_num} rounds. Total events: {len(self.events)}")

    async def interview_agent(self, agent_name: str, question: str) -> str:
        """Ask an agent a question (Big Brother interview feature)."""
        agent = next((a for a in self.agents if a.persona.name == agent_name), None)
        if not agent:
            return f"Agent '{agent_name}' not found."

        context = agent.get_interview_context()
        prompt = f"{context}\n\nViewer's question: {question}"

        try:
            if self.engine.backend == "anthropic":
                response = await self.engine._call_anthropic(prompt)
            elif self.engine.backend == "ollama":
                response = await self.engine._call_ollama(prompt)
            else:
                response = "Interview not available."
            # Store interview so it feeds back into agent's decision context
            agent.interview_history.append((question, response))
            return response
        except Exception as e:
            return f"Interview error: {e}"

    async def refresh_status_cache(self) -> None:
        """Refresh cached community data from the API.

        Called once per round from the simulation loop so get_status() can
        serve the viewer without making self-referencing HTTP calls (which
        cause httpx.ReadError under uvicorn's single-worker concurrency).
        """
        try:
            self._cached_community = await self.client.get_community(self.community_id)
        except Exception:
            pass  # keep stale cache
        try:
            self._cached_members = await self.client.get_members(self.community_id)
        except Exception:
            pass
        try:
            self._cached_variables = await self.client.get_variables(self.community_id)
        except Exception:
            pass

    async def get_status(self) -> dict:
        """Get current simulation status for the viewer.

        Uses cached data from refresh_status_cache() to avoid self-referencing
        HTTP calls that cause httpx.ReadError.
        """
        community = self._cached_community or {
            "id": self.community_id, "name": self.community_name, "member_count": len(self.agents)
        }
        members = self._cached_members or []
        variables = self._cached_variables or {}
        community["variables"] = variables

        return {
            "community": community,
            "round": self._round,
            "paused": self._paused,
            "auto_pause": {
                "every": self._auto_pause_every,
                "events_since_resume": self._events_since_resume,
            },
            "newcomers": self.newcomer_users,
            "agents": [
                {
                    "name": a.persona.name,
                    "role": a.persona.role,
                    "user_id": a.user_id,
                    "actions_taken": len(a.action_history),
                    "last_action": (
                        a.action_history[-1].action_type
                        if a.action_history else "none"
                    ),
                }
                for a in self.agents
            ],
            "members": members,
            "total_events": len(self.events),
            "bb_user_id": self._bb_user_id,
            "llm": {
                "backend": self.engine.backend,
                "model": self.engine.model,
                "preset": self._current_llm_preset(),
                **self.engine.stats,
            },
            "recent_events": [
                {
                    "agent": e.agent_name,
                    "action": e.action_type,
                    "details": e.details,
                    "reason": e.reason,
                    "time": e.timestamp.isoformat(),
                }
                for e in self.events[-20:]
            ],
        }

    async def cleanup(self) -> None:
        await self.client.close()
        if self.memory_store:
            await self.memory_store.close()
        if self.tkg_client:
            await self.tkg_client.close()
