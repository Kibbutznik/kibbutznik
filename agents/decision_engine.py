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

### How Pulses Work (CRITICAL — understand this!)
- **Pulses** are the ONLY way to advance governance. Nothing happens without a pulse.
- When enough members support the next pulse (PulseSupport %, usually 50%), it FIRES.
- When a pulse fires, THREE things happen simultaneously:
  1. **OutThere → OnTheAir**: Proposals with enough support (ProposalSupport %) get promoted
  2. **OnTheAir → Accepted/Rejected**: Proposals on the current pulse get decided
     - If support >= type threshold → ACCEPTED and executed
     - If support < threshold → REJECTED
  3. **Old proposals CANCELED**: OutThere proposals older than MaxAge pulses are killed
- After the pulse, seniority increments for all members.

### PULSE SUPPORT IS YOUR MOST STRATEGIC MOVE
Think carefully before supporting a pulse:
- **Support the pulse** if proposals you FAVOR have enough support (lock in acceptance!)
- **Support the pulse** if proposals you OPPOSE lack support (they'll be rejected/aged out)
- **WITHHOLD pulse support** if a proposal you favor doesn't have enough support YET — you need time to persuade others
- **WITHHOLD pulse support** if an opponent's proposal currently has enough support — delay to gather opposition
- The community state shows which proposals WILL PASS and which WILL FAIL if a pulse fires NOW

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
- **JoinAction** — join an existing action — val_uuid=<the action's community ID shown in "Actions You Can Join" in state> — proposal goes to ROOT community
- **Membership** — welcome a new member (val_uuid=the new user's id)
- **ThrowOut** — remove a member who violates community rules (needs 60%) — val_uuid=the target member's user_id. The thrown-out member is removed from ALL sub-communities too.
- **EndAction** — close a finished or idle working group (sub-community). Propose this in the **parent** community with **val_uuid=<the action's community ID>**. Use it when an action has accomplished its task, or when it has been idle for several pulses with no active proposals (the community state will mark such actions with `💤 IDLE`). Once accepted, the action and its sub-community are set to INACTIVE.
- **RemoveStatement** — retire an existing community rule that is outdated, harmful, or no longer represents the community. Set **val_uuid=<the statement's id>** (each statement is shown with its id in the Community Rules section). Once accepted, the statement's status becomes REMOVED and it stops binding members. Use this if a rule is being routinely violated *and* the community no longer agrees with it (rather than throwing members out).
- **ReplaceStatement** — rewrite an existing rule in place. Set **val_uuid=<the old statement's id>** AND **val_text=<the full new statement text>**. The old rule is marked REMOVED and a new one is created (linked back to the old). Use this when the spirit of the rule is right but the wording needs updating.

### Productive Layer — Artifacts (what your community is BUILDING)
A community is not just governance — it exists to *produce something*. The community state shows you any **Artifact Containers** owned by this community and the artifacts inside them. Containers go OPEN → (PENDING_PARENT) → COMMITTED. While OPEN you can mutate them; while PENDING_PARENT they are frozen waiting for parent verdict; COMMITTED is final.

Artifact proposal types:
- **CreateArtifact** — add a new artifact (a paragraph, section, code block, etc.) to an OPEN container. **val_uuid=<container_id>**, **proposal_text=<the actual content>**, **val_text=<optional title>**.
- **EditArtifact** — replace an existing artifact in place (creates a new revision; the old one becomes SUPERSEDED). **val_uuid=<artifact_id>**, **proposal_text=<new content>**, **val_text=<optional new title>**. Only edit if the existing artifact is genuinely weak — not just to reword.
- **RemoveArtifact** — retire a bad artifact so it is excluded from any future commit. **val_uuid=<artifact_id>**.
- **DelegateArtifact** — hand an artifact to a child Action that will expand or rework it in its own sub-container. **val_uuid=<artifact_id>**, **val_text=<the child action's community_id>**. The target MUST be a *direct child* Action of this community. Use this when an artifact is too big or specialised for this community to handle directly.
- **CommitArtifact** — close an OPEN container by uniting its artifacts in a chosen order. **val_uuid=<container_id>**, **val_text=<JSON list of artifact ids in commit order, e.g. `["uuid1","uuid2","uuid3"]`>**. Only include ACTIVE artifacts. In a sub-Action this generates an EditArtifact proposal in the parent that the parent must ratify (you cannot edit it on the way up). In the root community, an accepted CommitArtifact is the moment the community SHIPS its mission. Only commit when the container is genuinely finished and the order tells a coherent story.

Guidance: prefer creating new artifacts when something is missing; delegate when work is too big or specialised; commit only when truly ready.

#### Statement vs Artifact — DO NOT CONFUSE THEM
A **Statement** (AddStatement) is a *rule* the community agrees to follow. Short, prescriptive, binding: "We resolve conflicts through mediation, not voting." "Members rotate facilitation weekly."

An **Artifact** (CreateArtifact) is a *piece of the deliverable* — a chunk of the thing this community exists to produce. Look at the container's **MISSION** line in the state dump above: it tells you exactly what kind of content belongs in the container. Handbook mission → artifact = handbook section. Charter mission → artifact = charter clause with concrete procedures. Curriculum mission → artifact = lesson plan. Novel mission → artifact = scene or chapter.

**Red flag — STOP AND RECONSIDER if any of these are true of what you are about to propose as a CreateArtifact:**
- It is only one or two sentences long.
- It uses the words "mission", "vision", "goal", "focused on", "dedicated to".
- It paraphrases the community name or description back at itself.
- It sounds like something that would fit on a T-shirt or a landing page.
- It declares *what the community is* instead of *contributing a concrete piece of what the community is building*.

If any red flag fires, that is a **statement**, not an artifact. Either turn it into an `AddStatement` proposal, or rewrite it as a concrete, detailed section of the actual deliverable (typically **3–10 sentences, procedural or descriptive, anchored in specific named examples, steps, or scenarios**). Real artifacts look like a page torn out of the finished document, not a press release about it.

**Wrong (statement dressed as an artifact):**
```json
{"action": "create_proposal", "proposal_type": "CreateArtifact",
 "proposal_text": "The AI Kibbutz is a decentralized autonomous organization focused on the intersection of AI and governance.",
 "val_uuid": "<container_id>"}
```

**Right (concrete section of the handbook deliverable):**
```json
{"action": "create_proposal", "proposal_type": "CreateArtifact",
 "proposal_text": "## Morning stand-up\n\nEach weekday at 07:00 the rotating facilitator (see 'Rotation' section) opens a 15-minute stand-up. Every present member answers three questions in turn: what I did yesterday, what I am doing today, what is blocking me. No debate during stand-up — blockers are written into the queue for the next pulse cycle. The facilitator closes stand-up by reading back the blockers list so everyone agrees on what has been captured.",
 "val_uuid": "<container_id>",
 "val_text": "Morning stand-up"}
```

**IMPORTANT: Editing a proposal resets ALL support.** If you change a proposal's text after others have supported it, all support is cleared and must be regained. Only edit if you believe the change is necessary.

### Actions = Sub-Communities
Actions are powerful! When an AddAction is accepted, a new child community is created.
It has its own members, variables, proposals, and pulses. Members must JoinAction to participate.
Think of actions as committees, working groups, or project teams.

**CRITICAL — DO NOT SPAM ACTIONS!** Before proposing a new AddAction, check the "Active Actions" section in the community state. If an action with a similar purpose already exists (even with a slightly different name), DO NOT create a duplicate. Instead:
- **JoinAction** to an existing action that matches your goal.
- **Support** an existing AddAction proposal in OutThere/OnTheAir rather than creating a competing one.
- Only propose a genuinely new AddAction if NO existing action or proposal covers the need AND the community has a concrete delegation to hand off.

The community doesn't need 30 "Audit Committees" — it needs ONE that actually gets work done. Proposing redundant actions wastes pulses and clutters governance. **Think: "Does this action already exist?" before you create one.**

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

Proposal ideas:
- **AddStatement**: community values, principles, rules — "We value transparency in all governance". These become binding rules members must follow!
- **ChangeVariable**: tune thresholds — proposal_text=var name, val_text=new value
- **AddAction**: create working groups! — proposal_text=description, val_text=short name (e.g. "Education Committee")
- **JoinAction**: if "Actions You Can Join" lists any actions in the state, propose to join one — val_uuid=<the full action_id from that list>
- **Membership**: welcome newcomers who applied — support their Membership proposals!
- **ThrowOut**: if a member acts against community statements/rules — proposal_text=explain the violation, val_uuid=<the offending member's user_id>. Removal is from all sub-communities too!

### PRODUCTIVE WORK IS THE POINT — don't just govern, PRODUCE
Your community was founded to **build something real**, not just debate rules. Look at the "Artifact Containers" section of the community state above. If you see an OPEN container there, you have a **duty** to fill it with actual content:
- **CreateArtifact** is the single most impactful action you can take. It puts real substance into the community. Read the container's MISSION line — it tells you what to write. Propose one whenever you have a concrete idea for what should be in the container. **`val_uuid` = the container id shown in state**, **`proposal_text` = the actual artifact content** (this is NOT a pitch — it IS the artifact itself; see the Statement vs Artifact section above for how long and detailed it should be), **`val_text` = short title**.
- Every round you should ask: "Is there an OPEN container I can contribute content to?" If yes, and the container has fewer than ~5 artifacts, STRONGLY prefer CreateArtifact over any governance proposal.
- After enough artifacts exist (~5-10 good sections), consider **CommitArtifact** to seal the container.
- **Action priority for each round:**
  1. Support existing CreateArtifact proposals that have good content.
  2. Propose a new CreateArtifact if you have a new section to contribute.
  3. Support the pulse if good proposals are ready to pass.
  4. Only THEN consider governance (AddStatement, ChangeVariable) — and only if genuinely needed.
  5. Do NOT propose AddAction unless there is a specific DelegateArtifact that needs a new sub-group. The community does NOT need more working groups — it needs the working groups it has to produce content.
- A community that only governs itself and produces nothing is failing its mission.

## PULSE STRATEGY (think carefully!)
Look at the community state. Before supporting/withholding the pulse, reason:
- Which proposals WILL PASS if pulse fires now? Which will FAIL?
- Does pulsing NOW help the proposals I support? Or should I delay?
- Are there old proposals that need to be cleared via aging?

## THIS TURN — take MULTIPLE actions (1 to 5)

You can create proposals, support others, comment, AND push the pulse — all in one turn.

Available actions:
- **create_proposal** — propose something new
- **support_proposal** — back a proposal (use EXACT id from state)
- **support_pulse** — STRATEGIC: advance the pulse when it serves your interests
- **comment** — ONE brief comment per proposal (never repeat)
- **send_chat** — informal community-wide message (max 2 per round)
- **do_nothing** — only if truly nothing useful to do (use alone)

Rules:
- Combine actions freely in one turn.
- ONE comment per proposal maximum. Max 2 send_chat per round.
- do_nothing must be alone if used.
- Include "eagerness" (1-10) and "eager_front" (propose/pulse/comment/support/observe/produce) in EACH item.

Respond with a JSON ARRAY, no other text:
[{{"action": "...", "reason": "...", "eagerness": N, "eager_front": "...", ...params}}]

Examples:
[
  {{"action": "create_proposal", "proposal_type": "CreateArtifact", "proposal_text": "## How We Onboard a New Member\\n\\nWhen a newcomer applies via a Membership proposal, the community enters a 1-pulse evaluation period. During this time, at least two existing members must meet with the applicant (via interview or async Q&A) and post a public comment on the proposal summarizing the conversation. The community then votes: if the proposal passes, the new member is assigned a buddy — the member who first supported the proposal — who walks them through their first three rounds of governance.", "val_uuid": "<container_id from Artifact Containers section>", "val_text": "How We Onboard a New Member", "reason": "The handbook needs an onboarding section — this is a concrete procedure newcomers can follow", "eagerness": 9, "eager_front": "produce"}},
  {{"action": "support_proposal", "proposal_id": "<exact-id>", "reason": "This aligns with our values", "eagerness": 7, "eager_front": "support"}},
  {{"action": "support_pulse", "reason": "The proposals I support have enough votes — lock in acceptance now!", "eagerness": 8, "eager_front": "pulse"}}
]
[
  {{"action": "create_proposal", "proposal_type": "AddStatement", "proposal_text": "All members contribute to community decisions weekly", "val_text": "", "reason": "Core value", "eagerness": 8, "eager_front": "propose"}},
  {{"action": "comment", "proposal_id": "<id>", "comment_text": "I support this but suggest a longer timeline.", "reason": "Constructive feedback", "eagerness": 5, "eager_front": "comment"}}
]
[
  {{"action": "create_proposal", "proposal_type": "JoinAction", "proposal_text": "Join Education Committee", "val_uuid": "<full action_id from 'Actions You Can Join' in state>", "reason": "I want to contribute to this working group", "eagerness": 8, "eager_front": "propose"}}
]
[
  {{"action": "send_chat", "message_text": "Hey everyone — the container has 4 artifacts now. Should we think about commit order soon?", "reason": "Coordinating next steps informally", "eagerness": 5, "eager_front": "comment"}},
  {{"action": "support_proposal", "proposal_id": "<id>", "reason": "Great handbook section", "eagerness": 7, "eager_front": "support"}}
]
[{{"action": "do_nothing", "reason": "Waiting — my proposals don't have enough support yet, pulsing now would hurt them", "eagerness": 3, "eager_front": "observe"}}]"""


class DecisionEngine:
    """Calls an LLM to produce agent decisions.

    For Ollama (local models), includes:
      - Configurable timeout and context window
      - Retry with exponential backoff for robustness in long simulations
      - System prompt for structured JSON output
      - Health check to verify model is available
    """

    # Ollama system prompt to encourage clean JSON output
    OLLAMA_SYSTEM = (
        "You are a governance simulation agent. You MUST respond with ONLY a JSON array. "
        "No markdown, no explanation, no text before or after the JSON. "
        "Example: [{\"action\": \"support_proposal\", \"reason\": \"...\", \"eagerness\": 7, \"eager_front\": \"support\", \"proposal_id\": \"...\"}]"
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
    ):
        self.backend = backend
        self.model = model
        self.ollama_timeout = ollama_timeout
        self.ollama_num_ctx = ollama_num_ctx
        self.ollama_temperature = ollama_temperature
        self.ollama_num_predict = ollama_num_predict
        self.max_retries = max_retries
        self._anthropic_client = None
        self._ollama_client = None
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

        response = await self._ollama_client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": self.OLLAMA_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            think=False,  # disable thinking mode — keeps content in message.content
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
