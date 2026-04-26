"""
KBZ Agent — an AI-powered community member.

Each agent has:
  - A persona (personality, background, communication style)
  - A user account in the KBZ system
  - Ability to observe community state ("browse")
  - LLM-powered decision making
  - Social skills (commenting, discussing)
  - An action history for self-awareness
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from agents.api_client import KBZAPIError, KBZClient
from agents.community_state import CommunitySnapshot, observe_community
from agents.decision_engine import AgentAction, DecisionEngine
from agents.memory import MemoryStore
from agents.memory_extractor import MemoryExtractor
from agents.memory_formatter import MemoryFormatter
from agents.persona import Persona
from agents.tkg_client import TKGClient

logger = logging.getLogger(__name__)


# Hard cap on outbound comment length. Agents over-produce prose (see the
# decision-engine prompt rule "HARD LENGTH CAP — 50 words MAX"). The prompt
# sets the expectation; this enforces it even when the LLM ignores the rule,
# which it does often. Truncation is silent and ends on a sentence boundary
# when one is available, with a trailing "…" so readers can tell.
_COMMENT_CHAR_CAP = 300


def _truncate_comment(text: str, cap: int = _COMMENT_CHAR_CAP) -> str:
    """Clamp comment text to `cap` chars, preferring to break on punctuation."""
    if not text:
        return text
    t = text.strip()
    if len(t) <= cap:
        return t
    # Look for a sensible break (sentence or clause) inside the last 60 chars
    # of the cap window; fall back to a hard cut.
    window_start = max(0, cap - 60)
    cut = -1
    for sep in (". ", "? ", "! ", "; ", ", "):
        idx = t.rfind(sep, window_start, cap)
        if idx > cut:
            cut = idx + len(sep) - 1
    if cut > 0:
        return t[: cut + 1].rstrip() + "…"
    return t[:cap].rstrip() + "…"


@dataclass
class ActionLog:
    """Record of an action taken by the agent."""
    timestamp: datetime
    action_type: str
    reason: str
    details: str
    success: bool
    eagerness: int = 5
    eager_front: str = "observe"
    ref_id: str | None = None      # full ID of the referenced proposal/entity (for viewer linking)


class Agent:
    """An AI-powered KBZ community member."""

    def __init__(
        self,
        persona: Persona,
        client: KBZClient,
        engine: DecisionEngine,
        user_id: str | None = None,
        user_name: str | None = None,
        memory_store: MemoryStore | None = None,
        tkg_client: TKGClient | None = None,
    ):
        self.persona = persona
        self.client = client
        self.engine = engine
        self.user_id = user_id
        self.user_name = user_name or persona.name.lower()
        self.community_id: str | None = None
        self.action_history: list[ActionLog] = []
        self.users_cache: dict[str, str] = {}  # user_id -> name
        self.supported_proposals: set[str] = set()  # track what we've supported
        self.supported_pulse_ids: set[str] = set()  # track pulse IDs we've already supported
        self.commented_proposals: set[str] = set()  # track proposals already commented on
        self.eagerness: int = 5           # current eagerness (updated after each LLM decision)
        self.eager_front: str = "observe" # current eager front
        self.rounds_since_acted: int = 0  # for orchestrator starvation prevention
        self.interview_history: list[tuple[str, str]] = []  # (question, answer) from viewer
        self._chat_this_round: int = 0  # rate limit: max 2 send_chat per round
        self._last_chat_read: datetime | None = None  # track when we last read chat for diff
        self.rounds_since_pulse: int = 0  # set by orchestrator before each round
        # Memory system
        self.memory_store = memory_store
        self.tkg_client = tkg_client
        self.memory_formatter = (
            MemoryFormatter(memory_store, self.users_cache, tkg_client=tkg_client)
            if memory_store else None
        )
        self.memory_extractor = MemoryExtractor(memory_store, self.users_cache) if memory_store else None
        self.current_round: int = 0  # set by orchestrator each round
        # Persistent working memory — the agent's one-line answer to
        # "what am I trying to accomplish this arc?" Persists across turns
        # until the agent explicitly updates it via `update_intention` in
        # a decision. Surfaced at the top of every prompt so the LLM is
        # reminded of its own running goal, not just reactive to the
        # current snapshot.
        self.current_intention: str = ""

    async def register(self) -> None:
        """Create the user account in the KBZ system."""
        if self.user_id:
            return
        try:
            user = await self.client.create_user(
                user_name=self.user_name,
                about=f"{self.persona.role}: {self.persona.background[:100]}",
            )
            self.user_id = user["id"]
            self.users_cache[self.user_id] = self.persona.name
            logger.info(f"[{self.persona.name}] Registered as {self.user_id}")
        except Exception as e:
            logger.error(f"[{self.persona.name}] Failed to register: {e}")
            raise

    async def join_community(self, community_id: str) -> None:
        """Set the community this agent operates in."""
        self.community_id = community_id

    async def observe(self) -> CommunitySnapshot:
        """Browse and understand the current community state."""
        if not self.community_id:
            raise ValueError("Agent has no community")
        chat_after = self._last_chat_read.isoformat() if self._last_chat_read else None
        snapshot = await observe_community(
            self.client, self.community_id,
            chat_after=chat_after,
            rounds_since_pulse=self.rounds_since_pulse,
        )
        # Update last-read timestamp to now so next round gets only new messages.
        self._last_chat_read = datetime.now(timezone.utc)

        # Update users cache with member info
        for m in snapshot.members:
            if m["user_id"] not in self.users_cache:
                try:
                    user = await self.client.get_user(m["user_id"])
                    self.users_cache[m["user_id"]] = user["user_name"]
                except Exception:
                    self.users_cache[m["user_id"]] = m["user_id"][:8]

        return snapshot

    async def think_and_act(self) -> list[ActionLog]:
        """
        The main agent loop: observe → think → act (multiple actions per turn).
        Returns a list of logs for every action taken this turn.
        """
        self._chat_this_round = 0  # reset per-round chat rate limit

        # 1. OBSERVE — browse the community
        snapshot = await self.observe()
        community_summary = snapshot.summarize(
            my_user_id=self.user_id,
            users_cache=self.users_cache,
            supported_proposals=self.supported_proposals,
        )

        # 2. READ MEMORY — fetch persistent memory context for LLM prompt
        memory_context = ""
        if self.memory_formatter and self.user_id:
            try:
                # Update the formatter's users_cache reference
                self.memory_formatter.users_cache = self.users_cache
                memory_context = await self.memory_formatter.build_memory_context(
                    self.user_id,
                    current_round=self.current_round,
                    current_intention=self.current_intention,
                )
            except Exception as e:
                logger.debug(f"[{self.persona.name}] Memory read failed: {e}")

        # 3. THINK — ask the LLM for a list of decisions
        history_strings = [
            f"[{log.timestamp.strftime('%H:%M')}] {log.action_type}: {log.details}"
            for log in self.action_history[-6:]
        ]

        all_active = snapshot.proposals_out_there + snapshot.proposals_on_the_air
        unsupported = [
            f"[{p['proposal_type']}] \"{p['proposal_text'][:60]}\" id={p['id'][:8]}"
            for p in all_active
            if p["id"] not in self.supported_proposals
        ]
        already_supported = [
            f"[{p['proposal_type']}] \"{p['proposal_text'][:60]}\" id={p['id'][:8]}"
            for p in all_active
            if p["id"] in self.supported_proposals
        ]

        consecutive_do_nothings = 0
        for log in reversed(self.action_history):
            if log.action_type == "do_nothing":
                consecutive_do_nothings += 1
            else:
                break

        total_active = len(snapshot.proposals_out_there) + len(snapshot.proposals_on_the_air)

        # Include recent viewer interviews so agent can adjust behaviour
        interview_ctx = ""
        if self.interview_history:
            recent_interviews = self.interview_history[-3:]  # last 3
            interview_ctx = "\n## Recent Viewer Interviews (a viewer asked you these questions — consider their requests)\n"
            for q, a in recent_interviews:
                interview_ctx += f"  Viewer asked: \"{q[:120]}\"\n  You answered: \"{a[:120]}\"\n\n"

        # Surface the last few API failures so the LLM can adjust.
        # Take the most-recent N failures (not just the very last
        # turn's) — a 8b model often needs to see the same error
        # twice before it stops repeating it. Cap at 5 for token
        # budget. De-dupe by `action_type:detail` so a bot stuck
        # in a loop doesn't blow the buffer with identical lines.
        recent_failures: list[str] = []
        seen_keys: set[str] = set()
        for log in reversed(self.action_history):
            if log.success:
                continue
            key = f"{log.action_type}:{log.details}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            recent_failures.append(f"{log.action_type}: {log.details}")
            if len(recent_failures) >= 5:
                break
        recent_failures.reverse()  # oldest-first reads more naturally

        decisions = await self.engine.decide(
            persona_name=self.persona.name,
            persona_role=self.persona.role,
            persona_background=self.persona.background,
            persona_decision_style=self.persona.decision_style,
            persona_communication_style=self.persona.communication_style,
            persona_trait_summary=self.persona.trait_summary(),
            community_summary=community_summary,
            action_history=history_strings,
            unsupported_proposals=unsupported,
            already_supported_proposals=already_supported,
            already_commented=list(self.commented_proposals),
            consecutive_do_nothings=consecutive_do_nothings,
            initiative=self.persona.traits.initiative,
            total_active_proposals=total_active,
            interview_context=interview_ctx,
            memory_context=memory_context,
            recent_failures=recent_failures,
        )

        # 3. ACT — execute each decision, applying guards per action
        logs: list[ActionLog] = []
        best_eagerness = 5
        best_eager_front = "observe"

        for decision in decisions:
            decision = self._apply_guards(decision, snapshot)

            log = await self._execute_action(decision, snapshot)
            log.eagerness = decision.eagerness
            log.eager_front = decision.eager_front

            self.action_history.append(log)
            logs.append(log)

            logger.info(
                f"[{self.persona.name}] {log.action_type}: {log.details} "
                f"({'OK' if log.success else 'FAIL'}) "
                f"[eager={decision.eagerness} {decision.eager_front}]"
            )

            if decision.action_type != "do_nothing" and decision.eagerness >= best_eagerness:
                best_eagerness = decision.eagerness
                best_eager_front = decision.eager_front

        # Update agent-level eagerness from the most eager real action this turn
        self.eagerness = best_eagerness
        self.eager_front = best_eager_front
        self.rounds_since_acted = 0

        # Harvest `update_intention` if the LLM supplied one on any decision
        # this turn. Last one wins. Keeps the running goal across turns so
        # multi-step plans (propose → wait → support → pulse) don't reset.
        for decision in decisions:
            new_intention = (decision.params.get("update_intention") or "").strip()
            if new_intention:
                # Trim to a single punchy line — this goes into every future prompt.
                self.current_intention = new_intention[:160]

        # 5. WRITE MEMORY — extract memories from this turn's actions
        if self.memory_extractor and self.user_id:
            try:
                self.memory_extractor.users_cache = self.users_cache
                await self.memory_extractor.extract_from_actions(
                    self.user_id, logs, snapshot, self.current_round,
                )
            except Exception as e:
                logger.debug(f"[{self.persona.name}] Memory write failed: {e}")

        return logs

    def _apply_guards(self, decision: AgentAction, snapshot: CommunitySnapshot) -> AgentAction:
        """Apply pulse guard and comment guard to a single decision."""
        # PULSE GUARD: no active proposals → pulse support is wasted
        if decision.action_type == "support_pulse":
            has_active = snapshot.proposals_on_the_air or snapshot.proposals_out_there
            if not has_active:
                logger.debug(
                    f"[{self.persona.name}] Pulse guard: no proposals — dropping support_pulse"
                )
                return AgentAction(
                    action_type="do_nothing",
                    reason="No proposals exist — pulse support would be wasted",
                    eagerness=decision.eagerness,
                    eager_front=decision.eager_front,
                )

        # COMMENT GUARD: already commented on this proposal → skip or redirect
        if decision.action_type in ("comment", "reply_comment"):
            pid = self._resolve_proposal_id(decision.params.get("proposal_id", ""), snapshot)
            if pid and pid in self.commented_proposals:
                all_active = snapshot.proposals_out_there + snapshot.proposals_on_the_air
                unsupported_now = [p for p in all_active if p["id"] not in self.supported_proposals]
                if unsupported_now:
                    target = unsupported_now[0]
                    logger.debug(
                        f"[{self.persona.name}] Comment guard: already commented on {pid[:8]}, "
                        f"redirecting to support_proposal"
                    )
                    return AgentAction(
                        action_type="support_proposal",
                        reason="Already commented — supporting instead",
                        params={"proposal_id": target["id"]},
                        eagerness=decision.eagerness,
                        eager_front="support",
                    )
                # Nothing unsupported either — drop the action
                logger.debug(
                    f"[{self.persona.name}] Comment guard: already commented on {pid[:8]}, dropping"
                )
                return AgentAction(
                    action_type="do_nothing",
                    reason="Already commented on this proposal",
                    eagerness=decision.eagerness,
                    eager_front=decision.eager_front,
                )

        return decision

    # Proposal type names — never valid as IDs
    _PROPOSAL_TYPES = frozenset({
        "AddStatement", "RemoveStatement", "ReplaceStatement", "ChangeVariable",
        "AddAction", "EndAction", "Membership", "ThrowOut", "JoinAction",
        "CreateArtifact", "EditArtifact", "RemoveArtifact",
        "DelegateArtifact", "CommitArtifact",
    })
    # Valid UUID prefix: only hex chars and dashes
    _UUID_PREFIX_RE = re.compile(r'^[0-9a-fA-F\-]+$')

    def _resolve_proposal_id(self, short_id: str, snapshot: CommunitySnapshot) -> str:
        """Resolve a potentially truncated proposal ID to a full UUID."""
        if not short_id:
            return ""
        # Reject if it's a known proposal type name (LLM confusion)
        if short_id in self._PROPOSAL_TYPES:
            logger.warning(f"[{self.persona.name}] Got proposal type name '{short_id}' as proposal_id — ignoring")
            return ""
        # Reject strings that can't be UUID prefixes (no letters/dashes pattern)
        if not self._UUID_PREFIX_RE.match(short_id):
            logger.warning(f"[{self.persona.name}] Invalid proposal_id format '{short_id[:20]}' — ignoring")
            return ""
        # If it looks like a full UUID already, return as-is
        if len(short_id) >= 36:
            return short_id
        # Search all known proposals for a prefix match
        all_proposals = (
            snapshot.proposals_out_there
            + snapshot.proposals_on_the_air
            + snapshot.proposals_draft
            + snapshot.recent_accepted
            + snapshot.recent_rejected
        )
        for p in all_proposals:
            if p["id"].startswith(short_id):
                return p["id"]
        return ""

    def _resolve_comment_id(self, short_id: str, snapshot: CommunitySnapshot) -> str:
        """Resolve a potentially truncated comment ID to a full UUID."""
        if not short_id or len(short_id) >= 36:
            return short_id
        for comments in snapshot.proposal_comments.values():
            for c in comments:
                if c["id"].startswith(short_id):
                    return c["id"]
        return short_id

    def _resolve_val_uuid(self, short_id: str, snapshot: CommunitySnapshot) -> str:
        """Resolve a potentially truncated val_uuid to a full UUID.

        Searches across artifacts, containers, actions, statements, and members.
        """
        if not short_id:
            return ""
        if len(short_id) >= 36:
            return short_id
        # Search artifacts
        for arts in snapshot.container_artifacts.values():
            for a in arts:
                if a["id"].startswith(short_id):
                    return a["id"]
        # Search containers
        for c in snapshot.containers:
            if c["id"].startswith(short_id):
                return c["id"]
        # Search actions
        for a in snapshot.actions:
            if a["action_id"].startswith(short_id):
                return a["action_id"]
        # Search statements
        for s in snapshot.statements:
            if s["id"].startswith(short_id):
                return s["id"]
        # Search members
        for m in snapshot.members:
            if m["user_id"].startswith(short_id):
                return m["user_id"]
        return short_id  # return as-is if no match found

    async def _execute_action(self, decision: AgentAction, snapshot: CommunitySnapshot) -> ActionLog:
        """Execute the decided action via the API."""
        now = datetime.now(timezone.utc)

        try:
            if decision.action_type == "support_pulse":
                next_pulse = snapshot.next_pulse
                if next_pulse and next_pulse["id"] in self.supported_pulse_ids:
                    return ActionLog(now, "do_nothing", decision.reason,
                                     f"Already supported pulse {next_pulse['id'][:8]}", False)
                result = await self.client.support_pulse(self.community_id, self.user_id)
                if result.get("status") == "already_supported":
                    if next_pulse:
                        self.supported_pulse_ids.add(next_pulse["id"])
                    return ActionLog(now, "support_pulse", decision.reason, "Already supported pulse (sync)", True)
                if next_pulse:
                    self.supported_pulse_ids.add(next_pulse["id"])
                # When a new pulse is triggered, reset so we can support the new one
                if result.get("pulse_triggered"):
                    self.supported_pulse_ids.clear()
                return ActionLog(now, "support_pulse", decision.reason, "Supported next pulse", True)

            elif decision.action_type == "support_proposal":
                pid = self._resolve_proposal_id(decision.params.get("proposal_id", ""), snapshot)
                if not pid:
                    return ActionLog(now, "do_nothing", decision.reason, "Could not resolve proposal ID", False)
                if pid in self.supported_proposals:
                    return ActionLog(now, "do_nothing", decision.reason, f"Already supported proposal {pid[:8]}", False, ref_id=pid)
                result = await self.client.support_proposal(pid, self.user_id)
                self.supported_proposals.add(pid)
                if result.get("status") == "already_supported":
                    return ActionLog(now, "support_proposal", decision.reason, f"Already supported {pid[:8]} (sync)", True, ref_id=pid)
                return ActionLog(now, "support_proposal", decision.reason, f"Supported proposal {pid[:8]}", True, ref_id=pid)

            elif decision.action_type == "create_proposal":
                ptype = decision.params.get("proposal_type", "AddStatement")
                ptext = decision.params.get("proposal_text", "")
                val_text = decision.params.get("val_text", "")
                raw_val_uuid = decision.params.get("val_uuid")
                val_uuid = self._resolve_val_uuid(raw_val_uuid or "", snapshot) if raw_val_uuid else None

                # The agent's reason becomes the proposal's pitch (the "why").
                # This used to be smooshed into proposal_text for a subset of
                # types; now pitch has its own column, so we persist it cleanly
                # for every proposal type and leave proposal_text untouched.
                pitch = (decision.reason or "").strip() or None

                # --- EditArtifact pre-flight validation ---
                if ptype == "EditArtifact":
                    all_artifact_ids = {
                        a["id"]
                        for arts in snapshot.container_artifacts.values()
                        for a in arts
                    }
                    if not val_uuid or val_uuid not in all_artifact_ids:
                        return ActionLog(now, "do_nothing", decision.reason,
                            f"EditArtifact skipped: artifact {(val_uuid or '')[:8]} not found in community", False)

                # --- CreateArtifact pre-flight validation ---
                if ptype == "CreateArtifact":
                    valid_container_ids = {c["id"] for c in snapshot.containers}
                    if not val_uuid or val_uuid not in valid_container_ids:
                        return ActionLog(now, "do_nothing", decision.reason,
                            f"CreateArtifact skipped: container {(val_uuid or '')[:8]} not found in community", False)

                # --- DelegateArtifact pre-flight validation ---
                if ptype == "DelegateArtifact":
                    artifact_id = val_uuid or ""
                    raw_action_id = (val_text or "").strip()
                    # Resolve short action ID in val_text
                    action_community_id = self._resolve_val_uuid(raw_action_id, snapshot) if raw_action_id else ""
                    val_text = action_community_id  # update for the API call

                    # Guard 1: artifact must exist in this community
                    all_artifact_ids = {
                        a["id"]
                        for arts in snapshot.container_artifacts.values()
                        for a in arts
                    }
                    if not artifact_id or artifact_id not in all_artifact_ids:
                        return ActionLog(now, "do_nothing", decision.reason,
                            f"DelegateArtifact skipped: artifact {artifact_id[:8] if artifact_id else '?'} not found in community", False)

                    # Guard 2: target action must exist and be approved in this community
                    valid_action_ids = {a["action_id"] for a in snapshot.actions}
                    if not action_community_id or action_community_id not in valid_action_ids:
                        return ActionLog(now, "do_nothing", decision.reason,
                            f"DelegateArtifact skipped: action {action_community_id[:8] if action_community_id else '?'} does not exist or is not yet approved", False)

                    # Guard 3: no existing pending/accepted DelegateArtifact for same artifact+action
                    all_proposals = (
                        snapshot.proposals_out_there
                        + snapshot.proposals_on_the_air
                        + snapshot.proposals_draft
                        + snapshot.recent_accepted
                    )
                    if any(
                        p.get("proposal_type") == "DelegateArtifact"
                        and p.get("val_uuid") == artifact_id
                        and (p.get("val_text") or "").strip() == action_community_id
                        for p in all_proposals
                    ):
                        return ActionLog(now, "do_nothing", decision.reason,
                            f"DelegateArtifact skipped: delegation of {artifact_id[:8]} → {action_community_id[:8]} already exists", False)

                # --- CommitArtifact: resolve short artifact IDs in val_text JSON list ---
                if ptype == "CommitArtifact" and val_text:
                    import json as _json
                    try:
                        art_ids = _json.loads(val_text)
                        if isinstance(art_ids, list):
                            resolved_ids = [self._resolve_val_uuid(aid, snapshot) for aid in art_ids]
                            val_text = _json.dumps(resolved_ids)
                    except (ValueError, TypeError):
                        pass  # leave val_text as-is if not valid JSON

                proposal = await self.client.create_proposal(
                    community_id=self.community_id,
                    user_id=self.user_id,
                    proposal_type=ptype,
                    proposal_text=ptext,
                    val_text=val_text,
                    val_uuid=val_uuid,
                    pitch=pitch,
                )
                # Auto-submit
                await self.client.submit_proposal(proposal["id"])
                # Auto-support own proposal
                await self.client.support_proposal(proposal["id"], self.user_id)
                self.supported_proposals.add(proposal["id"])

                return ActionLog(
                    now, "create_proposal", decision.reason,
                    f"Created [{ptype}] \"{ptext[:60]}\" (id: {proposal['id'][:8]})", True,
                    ref_id=proposal["id"],
                )

            elif decision.action_type == "comment":
                pid = self._resolve_proposal_id(decision.params.get("proposal_id", ""), snapshot)
                text = _truncate_comment(decision.params.get("comment_text", ""))
                if pid and text:
                    await self.client.add_comment("proposal", pid, self.user_id, text)
                    self.commented_proposals.add(pid)
                    return ActionLog(now, "comment", decision.reason, f"Commented on {pid[:8]}: \"{text[:60]}\"", True, ref_id=pid)
                return ActionLog(now, "comment", decision.reason, "Missing proposal_id or text", False)

            elif decision.action_type == "reply_comment":
                pid = self._resolve_proposal_id(decision.params.get("proposal_id", ""), snapshot)
                parent_id = self._resolve_comment_id(decision.params.get("parent_comment_id", ""), snapshot)
                text = _truncate_comment(decision.params.get("comment_text", ""))
                if pid and text:
                    try:
                        if parent_id:
                            await self.client.add_comment("proposal", pid, self.user_id, text, parent_id)
                            self.commented_proposals.add(pid)
                            return ActionLog(now, "reply_comment", decision.reason, f"Replied on {pid[:8]}: \"{text[:60]}\"", True, ref_id=pid)
                    except Exception:
                        pass
                    await self.client.add_comment("proposal", pid, self.user_id, text)
                    self.commented_proposals.add(pid)
                    return ActionLog(now, "comment", decision.reason, f"Commented on {pid[:8]}: \"{text[:60]}\"", True, ref_id=pid)
                return ActionLog(now, "reply_comment", decision.reason, "Missing params", False)

            elif decision.action_type == "vote_comment":
                cid = self._resolve_comment_id(decision.params.get("comment_id", ""), snapshot)
                delta = decision.params.get("delta", 1)
                if cid:
                    await self.client.vote_comment(cid, delta)
                    direction = "upvoted" if delta > 0 else "downvoted"
                    return ActionLog(now, "vote_comment", decision.reason, f"{direction} comment {cid[:8]}", True)
                return ActionLog(now, "vote_comment", decision.reason, "Missing comment_id", False)

            elif decision.action_type == "send_chat":
                if self._chat_this_round >= 2:
                    return ActionLog(now, "send_chat", decision.reason, "Rate limited (max 2 per round)", False)
                text = decision.params.get("message_text", "")
                if text and self.community_id:
                    await self.client.add_comment("community", self.community_id, self.user_id, text)
                    self._chat_this_round += 1
                    return ActionLog(now, "send_chat", decision.reason, f"Chat: \"{text[:80]}\"", True)
                return ActionLog(now, "send_chat", decision.reason, "Missing message_text", False)

            elif decision.action_type == "do_nothing":
                return ActionLog(now, "do_nothing", decision.reason, "Chose to observe", True)

            else:
                return ActionLog(now, "unknown", decision.reason, f"Unknown action: {decision.action_type}", False)

        except KBZAPIError as e:
            # Surface the FastAPI `detail` so the next turn's prompt
            # tells the LLM *why* the call failed — not just "client
            # error 422". Cheap local models (Ollama 8b) repeat
            # invalid proposal shapes turn after turn otherwise,
            # which spends pulse cycles on nothing and makes the
            # community look stuck.
            return ActionLog(
                now, decision.action_type, decision.reason,
                f"HTTP {e.status_code}: {e.detail}", False,
            )
        except Exception as e:
            return ActionLog(now, decision.action_type, decision.reason, f"Error: {e}", False)

    def get_interview_context(self) -> str:
        """
        Build context for when a viewer asks this agent a question.
        Used in the Big Brother "ask the bot" feature.
        """
        from agents.decision_engine import KBZ_RULES
        recent_actions = "\n".join(
            f"- {log.action_type}: {log.details} ({log.reason})"
            for log in self.action_history[-20:]
        )
        return f"""You are {self.persona.name}, {self.persona.role}.

## Your Personality
{self.persona.background}

## Your Communication Style
{self.persona.communication_style}

{KBZ_RULES}

## Your Recent Actions in the Community
{recent_actions or "No actions yet."}

Answer the viewer's question in character. Be honest about your motivations
and decisions. Refer to specific actions you've taken and explain your reasoning.
Stay in character with your communication style."""
