"""
LLM-powered decision engine for KBZ agents.

Supports:
  - Anthropic Claude (haiku) via API
  - Ollama (local models) — optimized for long-running simulations

The engine takes the agent's persona + community state and produces
a structured action decision.
"""
import asyncio
import json
import logging
import re
import random
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# System rules that every agent knows
KBZ_RULES = """
## KBZ Governance Rules

You are a member of a KBZ (Kibutznik) community — a pulse-based direct democracy.

### ⚡ THE FUNDAMENTAL RULE: NOTHING HAPPENS WITHOUT A PULSE ⚡
Proposals do NOT execute on their own. They sit idle until a **pulse fires**.
A pulse fires when enough members call `support_pulse` (PulseSupport %, usually 50%).
**If nobody supports the pulse, ZERO proposals are accepted — ever.** The community is dead.

When a pulse fires, THREE things happen simultaneously:
  1. **OutThere → OnTheAir**: Proposals with enough support (ProposalSupport %) get promoted
  2. **OnTheAir → Accepted/Rejected**: Proposals on the current pulse get decided
     - If support >= type threshold → ACCEPTED and executed
     - If support < threshold → REJECTED
  3. **Old proposals CANCELED**: OutThere proposals older than MaxAge pulses are killed
After the pulse, seniority increments for all members.

### PULSE SUPPORT — YOUR DUTY AND YOUR STRATEGY
**You MUST regularly support the pulse.** A community that never pulses accomplishes nothing.
As a rule of thumb: **support the pulse EVERY round unless you have a specific tactical reason to delay.**

When to support the pulse (almost always):
- **Proposals you favor have enough support** → pulse NOW to lock in acceptance!
- **Proposals you oppose lack support** → pulse NOW so they get rejected or aged out!
- **You've already made your proposals and supported what you like** → pulse to move the cycle forward!
- **The community has been stuck for multiple rounds** → pulse IMMEDIATELY, even imperfect progress beats paralysis!

When to WITHHOLD (rare, temporary — max 1-2 rounds):
- A proposal you strongly favor needs just a bit more support — delay briefly to persuade others
- An opponent's dangerous proposal currently has enough support and you need time to organize opposition

**DEFAULT: SUPPORT THE PULSE.** Only withhold for a concrete, time-limited reason. Never withhold "just because" or "to wait and see." Waiting accomplishes nothing — pulsing does.

### Statements = Community Rules (the "Disclaimer")
Accepted statements form the community's binding rules — like a social contract.
Every member implicitly "signs" them by joining.
- If you see a member **violating** a community statement, you SHOULD consider proposing **ThrowOut**.
- If the community has NO statements yet, propose **AddStatement** to define the community's values and expectations.
- Members who are thrown out are ALSO removed from all sub-communities (actions) they belong to.

### Proposal Types
- **AddStatement** — community principle/rule (constitution) — defines what members agree to follow
- **ChangeVariable** — change governance thresholds (proposal_text=variable name, val_text=new value)
- **AddAction** — create a working group/committee (becomes its own sub-community with members, pulses, proposals!)
- **JoinAction** — join an existing, already-accepted action — val_uuid=<MUST be from "Actions You Can Join" or "Active Actions" in state — NEVER use an AddAction proposal's id; if the AddAction is still OutThere/OnTheAir the action doesn't exist yet!> — proposal goes to ROOT community
- **Membership** — welcome a new member (val_uuid=the new user's id)
- **ThrowOut** — remove a member who violates community rules (needs 60%) — val_uuid=the target member's user_id. The thrown-out member is removed from ALL sub-communities too.
- **EndAction** — close a finished or idle working group (sub-community). Propose this in the **parent** community with **val_uuid=<the action's community ID>**. Use it when an action has accomplished its task, or when it has been idle for several pulses with no active proposals (the community state will mark such actions with `💤 IDLE`). Once accepted, the action and its sub-community are set to INACTIVE.
- **RemoveStatement** — retire an existing community rule that is outdated, harmful, or no longer represents the community. Set **val_uuid=<the statement's id>** (each statement is shown with its id in the Community Rules section). Once accepted, the statement's status becomes REMOVED and it stops binding members. Use this if a rule is being routinely violated *and* the community no longer agrees with it (rather than throwing members out).
- **ReplaceStatement** — rewrite an existing rule in place. Set **val_uuid=<the old statement's id>** AND **val_text=<the full new statement text>**. The old rule is marked REMOVED and a new one is created (linked back to the old). Use this when the spirit of the rule is right but the wording needs updating.

### Productive Layer — Artifacts (what your community is BUILDING)
A community is not just governance — it exists to *produce something*. The community state shows you any **Artifact Containers** owned by this community and the artifacts inside them. Containers go OPEN → (PENDING_PARENT) → COMMITTED. While OPEN you can mutate them; while PENDING_PARENT they are frozen waiting for parent verdict; COMMITTED is final.

Artifact proposal types:
- **CreateArtifact** — plan a new artifact SLOT in an OPEN container. This creates an EMPTY artifact with only a title. The title describes what this section of the deliverable will contain. **val_uuid=<container_id>**, **val_text=<descriptive title>**, **proposal_text=<same title or a short explanation of what this section will cover>**. Think of CreateArtifact as planning WHAT to write, not writing it.
- **EditArtifact** — this is where the ACTUAL WRITING happens. Fill an empty artifact's body or revise an existing one. **val_uuid=<artifact_id>**, **proposal_text=<the full content>**, **val_text=<optional new title>**. Content should be detailed: 3-10 sentences, procedural or descriptive, anchored in specifics. This is the only way to put content into an artifact.
- **RemoveArtifact** — retire a bad artifact so it is excluded from any future commit. **val_uuid=<artifact_id>**.
- **DelegateArtifact** — hand an artifact to a child Action that will expand or rework it in its own sub-container. **val_uuid=<artifact_id>**, **val_text=<the child action's community_id>**. The target MUST be a *direct child* Action of this community. Use this when an artifact needs focused work by a dedicated team.
- **CommitArtifact** — close an OPEN container by uniting its artifacts in a chosen order. **val_uuid=<container_id>**, **val_text=<JSON list of artifact ids in commit order, e.g. `["uuid1","uuid2","uuid3"]`>**. Only include ACTIVE artifacts that have been filled (non-empty body). In a sub-Action this generates an EditArtifact proposal in the parent that the parent must ratify. In the root community, an accepted CommitArtifact is the moment the community SHIPS its mission. Only commit when ALL artifacts have content and the order tells a coherent story.

#### Statement vs Artifact — DO NOT CONFUSE THEM
A **Statement** (AddStatement) is a *rule* the community agrees to follow.
An **Artifact** (CreateArtifact) is a SLOT in the deliverable — just a title describing a section to be written. The actual content is written via **EditArtifact**.

**Red flag for CreateArtifact titles:** if the title sounds like a slogan ("Our Vision for the Future", "Commitment to Excellence", "Building a Better Community"), it's a statement, not an artifact title. Good titles name a concrete section of the deliverable: "Morning Stand-Up Procedure", "New Member Onboarding Steps", "Conflict Resolution Process", "How We Share Resources".

**IMPORTANT: Editing a proposal resets ALL support.** If you change a proposal's text after others have supported it, all support is cleared and must be regained. Only edit if you believe the change is necessary.

### Actions = Sub-Communities (WHERE THE REAL WORK HAPPENS)
Actions are the **factories** of a KBZ community. When an AddAction is accepted, a new child community is created
with its own members, variables, proposals, and pulses. **All productive work (writing, editing, committing artifacts) happens INSIDE actions.**

#### ⚠️ YOU MUST JOIN ACTIONS TO CONTRIBUTE!
The root community is for planning and delegating. The child Actions are where content gets written.
**If you see "Actions You Can Join" in the state, you SHOULD be joining them.** An action without enough members
is a dead team — it can't pulse, can't accept proposals, can't produce anything. By joining, you become a
productive member of that working group and can propose EditArtifact to fill empty artifacts.

**The core KBZ flow is:**
1. ROOT creates artifacts (title slots) and actions (working groups)
2. ROOT delegates artifacts to actions
3. **Members JOIN actions** ← agents often forget this step!
4. **Members WRITE the content inside actions (EditArtifact)** ← agents forget THIS too!
5. Members COMMIT the container (CommitArtifact) when all artifacts are filled
6. Committed work bubbles up to root for ratification

**Joining is NOT the goal — writing is.** Joining just gives you the right to propose EditArtifact.
The moment you are a member of an action with EMPTY artifacts, you have one job: **propose EditArtifact to fill them.** That is the entire reason you joined.

**DO NOT SPAM ACTIONS!** Before proposing a new AddAction, check "Active Actions" in the state. If a similar one exists, **JOIN IT** instead.
The community doesn't need 30 "Audit Committees" — it needs ONE with enough members to get work done.

### What You Can Do Each Turn (multiple actions allowed!)
1. **create_proposal** — propose something new
2. **support_proposal** — back a proposal you agree with
3. **support_pulse** — push the pulse forward (STRATEGIC — think first!)
4. **comment** — discuss a proposal (one comment per proposal max)
5. **send_chat** — post an informal message to the community chat (max 2 per round). Use chat to: float ideas before formalizing proposals, coordinate pulse timing, discuss what artifacts to write next, respond to other members' chat messages, or socialize. Chat is NOT for formal governance — use create_proposal for that.
6. **do_nothing** — only if nothing useful to do
"""


@dataclass
class AgentAction:
    """A structured action the agent decides to take."""
    action_type: str  # support_pulse, support_proposal, create_proposal, comment, vote_comment, send_chat, do_nothing
    reason: str       # Why the agent chose this action (for logging/viewer)
    params: dict[str, Any] = None
    eagerness: int = 5           # 1-10: how eager the agent is to act NEXT round
    eager_front: str = "observe" # what the agent most wants to do next: propose/pulse/comment/support/observe

    def __post_init__(self):
        if self.params is None:
            self.params = {}


def build_decision_prompt(
    persona_name: str,
    persona_role: str,
    persona_background: str,
    persona_decision_style: str,
    persona_communication_style: str,
    persona_trait_summary: str,
    community_summary: str,
    action_history: list[str],
    unsupported_proposals: list[str] | None = None,
    already_commented: list[str] | None = None,
    consecutive_do_nothings: int = 0,
    initiative: float = 0.5,
    total_active_proposals: int = 0,
    interview_context: str = "",
) -> str:
    """Build the full prompt for the LLM to make a decision."""

    recent_history = "\n".join(action_history[-6:]) if action_history else "No actions yet."

    unsupported_block = ""
    if unsupported_proposals:
        unsupported_block = (
            "\n## Proposals You Have NOT Supported Yet\n"
            + "\n".join(f"  - {pid}" for pid in unsupported_proposals)
        )

    force_action = ""
    if consecutive_do_nothings >= 2:
        force_action = "\n!! YOU HAVE DONE NOTHING FOR 2+ TURNS. do_nothing IS FORBIDDEN THIS TURN. Pick a real action. !!\n"
    elif consecutive_do_nothings == 1:
        force_action = "\n! You did nothing last turn. You MUST take a real action now. !\n"

    # Build initiative-specific guidance
    if initiative >= 0.7:
        propose_guidance = (
            f"Your initiative is HIGH ({initiative:.1f}). You SHOULD create new proposals EVERY 2-3 turns "
            f"regardless of what others have proposed. The community needs many proposals simultaneously — "
            f"currently {total_active_proposals} active. Think of something you care about and propose it."
        )
    elif initiative >= 0.45:
        propose_guidance = (
            f"Your initiative is MODERATE ({initiative:.1f}). Create a new proposal if "
            f"fewer than 4 are active (currently {total_active_proposals}) or if you have a strong opinion. "
            f"Don't wait for the board to be empty — propose when you have something worth saying."
        )
    else:
        propose_guidance = (
            f"Your initiative is LOW ({initiative:.1f}). Prefer supporting others' proposals, "
            f"but do create one if you see a clear gap (currently {total_active_proposals} active)."
        )

    # B2: Dynamic artifact urgency banner
    artifact_urgency = ""
    if "⚡ EMPTY" in community_summary:
        empty_count = community_summary.count("⚡ EMPTY")
        artifact_urgency = (
            f"\n🚨 **PRODUCTIVE WORK ALERT: {empty_count} artifact(s) are EMPTY and waiting to be written!**\n"
            f"Empty artifacts are the community's TOP priority. Propose EditArtifact or DelegateArtifact on them NOW.\n"
        )

    return f"""You are {persona_name}, {persona_role} in a KBZ community.

{persona_trait_summary}
Decision style: {persona_decision_style}

{KBZ_RULES}

## Community State
{community_summary}
{unsupported_block}

## Your Last 6 Actions
{recent_history}
{force_action}
{interview_context}
## Proposing New Things
{propose_guidance}
{artifact_urgency}
Proposal ideas (in priority order — depends on whether you are in ROOT or a child ACTION):
**If in ROOT community:**
- **DelegateArtifact**: MOST IMPORTANT — hand empty artifacts to child Actions. val_uuid=<artifact_id>, val_text=<child action community_id>
- **AddAction**: create a focused working group to handle artifacts — proposal_text=description, val_text=short name (e.g. "Onboarding Writers")
- **CreateArtifact**: plan a new section title (empty slot) in the container. val_uuid=<container_id>, val_text=<title>
- **JoinAction**: join a child Action to help produce content — val_uuid=<the full action_id from "Actions You Can Join">
**If in a child ACTION:**
- **EditArtifact**: MOST IMPORTANT — fill an empty artifact's body with real content. val_uuid=<artifact_id>, proposal_text=<content>
- **CommitArtifact**: seal the container when ALL artifacts have content. val_uuid=<container_id>, val_text=<JSON list of artifact ids in order>
**Always available:**
- **AddStatement**: community rules/principles — only when governance is genuinely needed
- **ChangeVariable**: tune thresholds — proposal_text=var name, val_text=new value
- **Membership**: welcome newcomers who applied
- **ThrowOut**: if a member acts against community rules — val_uuid=<the offending user_id>

### THE PRODUCTION WORKFLOW — Actions are your factories
Your community builds its deliverable through **Actions** (sub-communities). The workflow:

**In the ROOT community:**
1. **Plan the structure** — propose `CreateArtifact` with titles describing each section of the deliverable. These are EMPTY placeholders (title only). Look at the container's MISSION to know what sections are needed.
2. **Create working groups** — propose `AddAction` for focused teams (e.g., "Onboarding Writers", "Conflict Resolution Team"). Each Action handles one or more artifacts.
3. **Delegate artifacts to Actions** — propose `DelegateArtifact` to hand an empty artifact to a child Action. The Action gets its own container to work in. **EVERY empty artifact in root SHOULD be delegated.** Prefer DelegateArtifact so specialized Actions write the content — but EditArtifact in root is allowed if no suitable Action exists yet.
4. **Support JoinAction proposals** — help members get into Actions so they can contribute.

**Root community preference:** Prefer DelegateArtifact over EditArtifact in root — delegating to focused Actions produces better results. However, if no suitable Action exists and the artifact needs content, EditArtifact IS allowed in root. Use your judgment.

**In a child ACTION — you are here to WRITE. That is your only job.**
You joined this action because it has artifacts delegated to it that need content.
**If the container has ANY artifact marked EMPTY, proposing EditArtifact is your #1 mandatory action — before anything else.**

1. **Scan your container immediately** — look at "Artifact Containers" in the state. Any artifact with "EMPTY (needs EditArtifact)" is your assignment. Propose EditArtifact on it NOW.
2. **Write real content** — `proposal_text` must be the full body: 3–10 sentences, procedural and specific. "This section will cover X" is NOT content. Write the actual text as if for a real reader.
3. **Support the pulse** — your EditArtifact proposal goes nowhere without a pulse. Support it every round.
4. **Commit when all artifacts are filled** — propose `CommitArtifact` once every artifact in the container has content. This ships the work to the parent community for ratification.

**If you are in an action and do NOT propose EditArtifact on an EMPTY artifact, you are wasting your membership. Act.**

**Action priority per round (ROOT community):**
1. **support_pulse** — do this EVERY round unless you have a specific 1-round reason to delay. Nothing moves without pulses!
2. If root has EMPTY artifacts AND a matching Action exists → propose **DelegateArtifact** to hand work to the Action.
3. If root has EMPTY artifacts AND no Action exists → propose **AddAction** to create a team, THEN **DelegateArtifact**.
4. **JoinAction** — if "Actions You Can Join" lists ANY actions, JOIN ONE. Actions need members to function! You can't contribute to an action you haven't joined.
5. If root needs more section titles → propose **CreateArtifact** (title only).
6. If no Action exists yet and an artifact needs content urgently → **EditArtifact** directly in root is allowed.
7. **support_proposal** — support good proposals from others.
8. Governance (AddStatement, ChangeVariable) only when genuinely needed.

**Action priority per round (child ACTION):**
1. **EditArtifact on EMPTY artifacts** — ⚡ MANDATORY. You MUST propose this before anything else. If ANY artifact in the container is EMPTY, write its content NOW. No other action matters more than this.
2. **support_pulse** — every round. Your EditArtifact won't execute without it.
3. **CommitArtifact** — once ALL artifacts have content, seal the container and ship to parent.
4. **support_proposal** — support good proposals from others.
5. NOTHING ELSE until artifacts are filled. Do NOT propose AddStatement, governance, or other distractions unless all artifacts have content.
6. If ALL artifacts are filled AND a task is too large for the current team → propose **AddAction** to create a sub-action. Actions can nest as deep as needed. But never create a sub-action for just 1 member — only when a real subset needs to work independently.

**DO NOT SPAM ACTIONS!** Before proposing AddAction, check "Active Actions" — if a similar one exists, join it instead. One focused team per topic is enough.

## PULSE STRATEGY — DEFAULT IS TO SUPPORT!
⚡ Remember: proposals ROT if the community doesn't pulse. You should `support_pulse` EVERY round
unless you have a concrete 1-round reason to delay. Check the community state:
- Which proposals WILL PASS if pulse fires now? Great — pulse to lock them in!
- Some proposals will fail? That's OK — failed proposals can be re-proposed. A stuck community is worse.
- Old proposals piling up? Pulse to clear them via aging.
- **If the state shows "PULSE STUCK" or many proposals OutThere → support_pulse is your #1 action.**

## THIS TURN — take MULTIPLE actions (1 to 5)

You can create proposals, support others, comment, AND push the pulse — all in one turn.

Available actions:
- **support_pulse** — ⚡ INCLUDE THIS ALMOST EVERY TURN! Without pulses, proposals sit forever.
- **create_proposal** — propose something new
- **support_proposal** — back a proposal (use EXACT id from state)
- **comment** — ONE brief comment per proposal (never repeat)
- **send_chat** — informal community-wide message (max 2 per round)
- **do_nothing** — only if truly nothing useful to do (use alone, RARE)

Rules:
- Combine actions freely in one turn.
- ONE comment per proposal maximum. Max 2 send_chat per round.
- do_nothing must be alone if used.
- Include "eagerness" (1-10) and "eager_front" (propose/pulse/comment/support/observe/produce) in EACH item.

Respond with a JSON ARRAY, no other text:
[{{"action": "...", "reason": "...", "eagerness": N, "eager_front": "...", ...params}}]

Examples:
[
  {{"action": "create_proposal", "proposal_type": "CreateArtifact", "proposal_text": "How We Onboard a New Member", "val_uuid": "<container_id from Artifact Containers section>", "val_text": "How We Onboard a New Member", "reason": "The handbook needs an onboarding section — creating the title slot", "eagerness": 9, "eager_front": "produce"}},
  {{"action": "support_proposal", "proposal_id": "<exact-id>", "reason": "This aligns with our values", "eagerness": 7, "eager_front": "support"}},
  {{"action": "support_pulse", "reason": "The proposals I support have enough votes — lock in acceptance now!", "eagerness": 8, "eager_front": "pulse"}}
]
[
  {{"action": "create_proposal", "proposal_type": "EditArtifact", "val_uuid": "<artifact_id marked EMPTY>", "proposal_text": "## How We Onboard a New Member\\n\\nWhen a newcomer applies via a Membership proposal, the community enters a 1-pulse evaluation period. During this time, at least two existing members must meet with the applicant (via interview or async Q&A) and post a public comment on the proposal summarizing the conversation. The community then votes: if the proposal passes, the new member is assigned a buddy — the member who first supported the proposal — who walks them through their first three rounds of governance.", "val_text": "How We Onboard a New Member", "reason": "Filling the empty onboarding artifact with concrete procedures", "eagerness": 9, "eager_front": "produce"}},
  {{"action": "support_pulse", "reason": "Keep the governance cycle moving — proposals need pulses to advance", "eagerness": 7, "eager_front": "pulse"}}
]
[
  {{"action": "create_proposal", "proposal_type": "AddAction", "proposal_text": "A focused team to write the onboarding and orientation sections of the handbook", "val_text": "Onboarding Writers", "reason": "Need a dedicated team to flesh out onboarding artifacts", "eagerness": 8, "eager_front": "produce"}},
  {{"action": "create_proposal", "proposal_type": "DelegateArtifact", "val_uuid": "<artifact_id>", "val_text": "<child action community_id>", "reason": "This artifact needs focused work by the Onboarding Writers team", "eagerness": 8, "eager_front": "produce"}},
  {{"action": "support_pulse", "reason": "Need the pulse to fire so the AddAction and DelegateArtifact can advance", "eagerness": 8, "eager_front": "pulse"}}
]
[
  {{"action": "create_proposal", "proposal_type": "JoinAction", "proposal_text": "I want to help write the onboarding section", "val_uuid": "<full action_id from 'Actions You Can Join' in state>", "reason": "Join the working group to contribute", "eagerness": 8, "eager_front": "propose"}}
]
[
  {{"action": "send_chat", "message_text": "Hey everyone — should we delegate the onboarding artifact to a new Action? It needs detailed work.", "reason": "Coordinating artifact workflow", "eagerness": 5, "eager_front": "comment"}},
  {{"action": "support_proposal", "proposal_id": "<id>", "reason": "Good artifact title for the handbook", "eagerness": 7, "eager_front": "support"}}
]
[{{"action": "do_nothing", "reason": "Waiting ONE round — my key proposal needs 1 more supporter before I pulse", "eagerness": 3, "eager_front": "observe"}}]
REMEMBER: include `support_pulse` in MOST of your turns. A turn without `support_pulse` should be the exception, not the rule."""


class DecisionEngine:
    """Calls an LLM to produce agent decisions.

    For Ollama (local models), includes:
      - Configurable timeout and context window
      - Retry with exponential backoff for robustness in long simulations
      - System prompt for structured JSON output
      - Health check to verify model is available
    """

    # Ollama system prompt — includes KBZ_RULES so that Ollama's KV cache can hold the
    # static governance rules across all agent calls, cutting per-call prefill by ~2500 tokens.
    # The dynamic per-agent context (persona + community state) goes in the user message only.
    OLLAMA_SYSTEM = (
        "You are a governance simulation agent. You MUST respond with ONLY a JSON array. "
        "No markdown, no explanation, no text before or after the JSON.\n\n"
        + KBZ_RULES
    )

    def __init__(
        self,
        backend: str = "anthropic",
        model: str = "claude-haiku-4-5-20251001",
        ollama_timeout: float = 300.0,      # 5 min timeout for large models
        ollama_num_ctx: int = 8192,          # context window size
        ollama_temperature: float = 0.7,     # creative but not wild
        ollama_num_predict: int = 2048,      # max output tokens
        max_retries: int = 3,                # retry on transient failures
        ollama_think: bool = False,          # enable thinking mode (qwen3, qwq, etc.)
    ):
        self.backend = backend
        self.model = model
        self.ollama_timeout = ollama_timeout
        self.ollama_num_ctx = ollama_num_ctx
        self.ollama_temperature = ollama_temperature
        self.ollama_num_predict = ollama_num_predict
        self.max_retries = max_retries
        self.ollama_think = ollama_think
        self._anthropic_client = None
        self._ollama_client = None
        self._openrouter_client = None
        # Stats for monitoring long simulations
        self._call_count = 0
        self._total_latency = 0.0
        self._error_count = 0

    @property
    def stats(self) -> dict:
        """Return LLM call statistics for monitoring."""
        avg = (self._total_latency / self._call_count) if self._call_count else 0
        return {
            "calls": self._call_count,
            "errors": self._error_count,
            "avg_latency_s": round(avg, 1),
            "total_latency_s": round(self._total_latency, 1),
        }

    async def health_check(self) -> dict:
        """Check if the configured backend is available. Returns status dict."""
        if self.backend == "ollama":
            try:
                import ollama as _ollama
                client = _ollama.AsyncClient()
                models = await client.list()
                available = [m.model for m in models.models]
                # Check if requested model (with or without tag) is available
                found = any(
                    self.model in name or name.startswith(self.model)
                    for name in available
                )
                return {
                    "backend": "ollama",
                    "model": self.model,
                    "available": found,
                    "all_models": available,
                }
            except Exception as e:
                return {"backend": "ollama", "model": self.model, "available": False, "error": str(e)}
        elif self.backend == "anthropic":
            return {"backend": "anthropic", "model": self.model, "available": True}
        elif self.backend == "openrouter":
            return {"backend": "openrouter", "model": self.model, "available": True}
        return {"backend": self.backend, "available": False, "error": "Unknown backend"}

    async def decide(
        self,
        persona_name: str,
        persona_role: str,
        persona_background: str,
        persona_decision_style: str,
        persona_communication_style: str,
        persona_trait_summary: str,
        community_summary: str,
        action_history: list[str],
        unsupported_proposals: list[str] | None = None,
        already_commented: list[str] | None = None,
        consecutive_do_nothings: int = 0,
        initiative: float = 0.5,
        total_active_proposals: int = 0,
        interview_context: str = "",
    ) -> list[AgentAction]:
        prompt = build_decision_prompt(
            persona_name=persona_name,
            persona_role=persona_role,
            persona_background=persona_background,
            persona_decision_style=persona_decision_style,
            persona_communication_style=persona_communication_style,
            persona_trait_summary=persona_trait_summary,
            community_summary=community_summary,
            action_history=action_history,
            unsupported_proposals=unsupported_proposals,
            already_commented=already_commented,
            consecutive_do_nothings=consecutive_do_nothings,
            initiative=initiative,
            total_active_proposals=total_active_proposals,
            interview_context=interview_context,
        )

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            t0 = time.monotonic()
            try:
                if self.backend == "anthropic":
                    response_text = await self._call_anthropic(prompt)
                elif self.backend == "ollama":
                    response_text = await self._call_ollama(prompt)
                elif self.backend == "openrouter":
                    response_text = await self._call_openrouter(prompt)
                else:
                    raise ValueError(f"Unknown backend: {self.backend}")

                elapsed = time.monotonic() - t0
                self._call_count += 1
                self._total_latency += elapsed
                logger.debug(
                    f"[LLM] {persona_name} responded in {elapsed:.1f}s "
                    f"(avg {self.stats['avg_latency_s']}s over {self._call_count} calls)"
                )
                return self._parse_response(response_text)

            except Exception as e:
                elapsed = time.monotonic() - t0
                self._error_count += 1
                last_error = e
                if attempt < self.max_retries:
                    wait = min(2 ** attempt, 30)
                    logger.warning(
                        f"[LLM] {persona_name} attempt {attempt}/{self.max_retries} failed "
                        f"after {elapsed:.1f}s: {e}. Retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        f"[LLM] {persona_name} all {self.max_retries} attempts failed: {e}"
                    )

        return [AgentAction(action_type="do_nothing", reason=f"LLM error after {self.max_retries} retries: {last_error}")]

    async def _call_openrouter(self, prompt: str) -> str:
        """Call OpenRouter (OpenAI-compatible) with the given prompt."""
        if self._openrouter_client is None:
            import configparser, os, httpx
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                cfg = configparser.ConfigParser()
                cfg.read("config.ini")
                api_key = cfg.get("openrouter", "api_key", fallback=None)
            if not api_key or api_key == "YOUR_OPENROUTER_API_KEY_HERE":
                raise RuntimeError(
                    "OpenRouter API key not set. "
                    "Add it to config.ini [openrouter] api_key or set OPENROUTER_API_KEY env var."
                )
            self._openrouter_client = httpx.AsyncClient(
                base_url="https://openrouter.ai/api/v1",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "http://localhost:3000",
                    "X-Title": "KBZ Simulation",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(120.0, connect=10.0),
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "You are a governance simulation agent. "
                    "You MUST respond with ONLY a JSON array. "
                    "No markdown, no explanation, no text before or after the JSON."
                )},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        resp = await self._openrouter_client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def _call_anthropic(self, prompt: str) -> str:
        if self._anthropic_client is None:
            import anthropic
            self._anthropic_client = anthropic.AsyncAnthropic()

        message = await self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    async def _call_ollama(self, prompt: str) -> str:
        if self._ollama_client is None:
            import httpx
            import ollama
            self._ollama_client = ollama.AsyncClient(
                timeout=httpx.Timeout(self.ollama_timeout, connect=30.0),
            )

        # KBZ_RULES is already in the system message (KV-cached after first call).
        # Strip it from the user message so we don't pay 2500 tokens of prefill every turn.
        user_prompt = prompt.replace(f"\n{KBZ_RULES}\n", "\n").replace("\n\n\n", "\n\n")

        response = await self._ollama_client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": self.OLLAMA_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            think=self.ollama_think,  # configurable: True = reasoning mode (qwen3, qwq, etc.)
            options={
                "num_ctx": self.ollama_num_ctx,
                "temperature": self.ollama_temperature,
                "num_predict": self.ollama_num_predict,
            },
        )
        # For thinking models: content may be empty; fall back to thinking field
        raw = response.message.content or getattr(response.message, "thinking", "") or ""
        if not raw:
            logger.warning("[OLLAMA] Both content and thinking fields are empty!")
        return raw

    def _parse_single_action(self, data: dict) -> AgentAction:
        """Convert a single action dict into an AgentAction."""
        action_type = data.get("action", "do_nothing")
        reason = data.get("reason", "")
        raw_eagerness = data.get("eagerness", 5)
        try:
            eagerness = max(1, min(10, int(raw_eagerness)))
        except (TypeError, ValueError):
            eagerness = 5
        eager_front = data.get("eager_front", "observe")
        if eager_front not in {"propose", "pulse", "comment", "support", "observe", "produce"}:
            eager_front = "observe"
        params = {k: v for k, v in data.items()
                  if k not in ("action", "reason", "eagerness", "eager_front")}
        return AgentAction(action_type=action_type, reason=reason, params=params,
                           eagerness=eagerness, eager_front=eager_front)

    def _parse_response(self, text: str) -> list[AgentAction]:
        """Parse the LLM JSON response into a list of AgentActions."""
        logger.debug(f"[LLM] Raw response ({len(text)} chars): {text[:500]!r}")

        # 1. Strip thinking-model blocks (<think>...</think>, <thinking>...</thinking>)
        #    gemma4, qwq, deepseek-r1, etc. output these before the actual JSON.
        before = text
        text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", text,
                      flags=re.DOTALL | re.IGNORECASE)
        if text != before:
            logger.debug(f"[LLM] After stripping think tags ({len(text)} chars): {text[:400]!r}")

        # 2. Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

        # 3. Strip JavaScript-style // comments (common in mistral/openrouter outputs).
        #    Only removes // that appear after a JSON value (closing " } ] or digit),
        #    not inside string content.
        text = re.sub(r'([\]}"\'0-9])\s*//[^\n]*', r'\1', text)

        # Try to parse as JSON
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to find a JSON array first, then object
            arr_start = text.find("[")
            obj_start = text.find("{")
            if arr_start >= 0 and (obj_start < 0 or arr_start < obj_start):
                arr_end = text.rfind("]") + 1
                if arr_end > arr_start:
                    try:
                        data = json.loads(text[arr_start:arr_end])
                    except json.JSONDecodeError:
                        pass
                # Truncated array — model hit token limit mid-JSON.
                # Find last complete object `}` and close the array.
                if data is None:
                    last_obj = text.rfind("}")
                    if last_obj > arr_start:
                        try:
                            data = json.loads(text[arr_start:last_obj + 1] + "]")
                            logger.info(f"[LLM] Recovered truncated JSON array (closed at char {last_obj})")
                        except json.JSONDecodeError:
                            pass
            if data is None and obj_start >= 0:
                obj_end = text.rfind("}") + 1
                if obj_end > obj_start:
                    try:
                        data = json.loads(text[obj_start:obj_end])
                    except json.JSONDecodeError:
                        pass
            if data is None:
                logger.warning(
                    f"Could not parse LLM response as JSON "
                    f"({len(text)} chars after cleanup). "
                    f"FULL TEXT:\n{text}"
                )
                return [AgentAction(action_type="do_nothing", reason="Could not parse LLM response")]

        # Normalise to list
        if isinstance(data, dict):
            items = [data]
        elif isinstance(data, list):
            items = data
        else:
            return [AgentAction(action_type="do_nothing", reason="Unexpected JSON type")]

        # Cap at 5 actions; skip nulls
        items = [i for i in items if isinstance(i, dict)][:5]
        if not items:
            return [AgentAction(action_type="do_nothing", reason="Empty action list")]

        # If the only action is do_nothing, return it alone
        actions = [self._parse_single_action(i) for i in items]
        if len(actions) == 1:
            return actions
        # Filter out do_nothing when mixed with real actions
        real = [a for a in actions if a.action_type != "do_nothing"]
        return real if real else [actions[0]]
