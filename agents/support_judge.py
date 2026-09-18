"""
Support votes for KBZ agents, decided with TypeSafe instead of the LLM.

`support_proposal` and `support_pulse` used to be two of the six actions the
LLM chose inside its one big JSON turn. That made the most consequential
thing an agent does — deciding which proposals pass and when the community
moves — a side effect of a long prompt whose support guidance had to be
retuned whenever the model changed: a faithful model over-pulsed (#217) and
rubber-stamped nearly every proposal (#218).

Here both votes come from small, typed judgments made by TypeSafe's System
One model (https://docs.typesafe.ai), composed by policy that lives in code:

  * Proposal votes. One request per agent turn asks, for every proposal the
    member has not backed yet: how much would THIS member want it, is it
    concrete, does it improve the text it replaces, would it damage the
    community, does a comment point out a real flaw in it, and does it
    repeat another proposal. `decide_proposal` turns those answers into a
    vote with thresholds you can read, test and change without re-prompting
    anything.

  * Pulse vote. Mostly arithmetic, so mostly code: for each open proposal,
    what would firing the pulse NOW do to it, and is that good or bad for
    this member given their votes? Jev is weak at counting and threshold
    comparisons, so none of that goes to the model. The one semantic signal
    — whether chat is asking members to hold or to pulse — rides along in
    the same request.

The LLM still writes proposals, comments and chat. It is shown how the
member voted, so its comments can explain the votes.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, field

from agents.community_state import CommunitySnapshot, tag_id
from agents.decision_engine import AgentAction
from agents.persona import Persona

logger = logging.getLogger(__name__)

# Pinned rather than "jev-latest": the thresholds below were measured
# against this version, and the alias moves when TypeSafe ships a new model.
TYPESAFE_MODEL = "jev-1.13.0"

# Proposals judged per turn. The rest wait a turn: a member does not have to
# rule on everything at once, and irrelevant state costs the model accuracy.
MAX_PROPOSALS_PER_TURN = 15

_TEXT_CAP = 1500
_COMMENT_CAP = 200
_CHAT_MESSAGES = 8

# ── Policy ────────────────────────────────────────────────────────────
# Noul answers are probabilities of "yes"; stance is a 0–3 Score. Measured
# with `python -m agents.bench_support` on jev-1.13.0: every judgment below
# separated its fixtures with a wide margin, except harm, which is why its
# bar sits higher than the rest.
CONCRETE_MIN = 0.5    # below → too vague to commit to (text types only)
IMPROVES_MIN = 0.5    # below → the rewrite is worse than what it replaces
DUPLICATE_MAX = 0.5   # above → repeats a proposal the member already backs
HARM_MAX = 0.7        # above → would damage the community. Bad-but-ordinary
                      # changes (a platitude rewrite, a conflicting rule)
                      # scored up to 0.6; removing a member without cause, 0.86+.
OBJECTION_MIN = 0.5   # at or above → a comment raises a real flaw...
OBJECTION_BAR = 0.5   # ...and the proposal must clear this much more stance
CHAT_SIGNAL_MIN = 0.5

MODES = ("auto", "typesafe", "llm")

_WHAT_IT_DOES = {
    "Membership": "admits a new member",
    "ThrowOut": "removes a member from the community",
    "AddStatement": "adds a binding community rule",
    "RemoveStatement": "retires an existing community rule",
    "ReplaceStatement": "rewrites an existing community rule",
    "ChangeVariable": "changes a governance setting",
    "AddAction": "creates a working group (a sub-community)",
    "EndAction": "shuts down a working group",
    "JoinAction": "adds its author to a working group",
    "Funding": "funds a working group from the community treasury",
    "Payment": "pays money out of the community treasury",
    "payBack": "decides how a working group repays the community",
    "Dividend": "distributes funds to members",
    "SetMembershipHandler": "hands membership decisions to a working group",
    "CreateArtifact": "adds an empty, titled section to the community's deliverable",
    "EditArtifact": "writes or rewrites a section of the community's deliverable",
    "RemoveArtifact": "removes a section from the community's deliverable",
    "DelegateArtifact": "hands a section of the deliverable to a working group",
    "CommitArtifact": "seals the deliverable in its current order",
}

# Types whose substance IS their wording, so vague text sinks them. For the
# rest (Membership, JoinAction, DelegateArtifact, ...) the change is the
# target, and a one-line description is normal, not a flaw.
_TEXT_TYPES = frozenset({
    "AddStatement", "ReplaceStatement", "ChangeVariable",
    "AddAction", "CreateArtifact", "EditArtifact",
})

# Duplicates. Where a proposal's effect is fully named by its target, two
# proposals are the same change exactly when their targets match, which is
# a lookup for code, not a judgment. Templated texts ("Alex wants to join the
# community" / "Sam wants to join the community") read alike to a model and
# are nothing alike. Only proposals that ARE their wording get compared by
# meaning. Competing EditArtifact / ReplaceStatement versions are
# alternatives, not copies; `improves` and stance judge each one.
_TARGET_FIELDS = {
    "Membership": ("val_uuid",),
    "ThrowOut": ("val_uuid",),
    "RemoveStatement": ("val_uuid",),
    "EndAction": ("val_uuid",),
    "RemoveArtifact": ("val_uuid",),
    "CommitArtifact": ("val_uuid",),
    "SetMembershipHandler": ("val_uuid",),
    "DelegateArtifact": ("val_uuid", "val_text"),
    "JoinAction": ("val_uuid", "user_id"),  # each member files their own
    "Funding": ("val_uuid", "val_text"),
    "Payment": ("val_uuid", "val_text"),
    "payBack": ("val_uuid", "val_text"),
    "Dividend": ("val_text",),
}
_SEMANTIC_DUPLICATE_TYPES = frozenset({"AddStatement", "AddAction", "CreateArtifact"})

_STANCE_LEVELS = [
    (
        "Opposes it: it works against what the member wants, or the member's "
        "decision style says to reject this kind of proposal"
    ),
    "Indifferent: it does not touch anything the member cares about",
    "Favors it: it helps something the member cares about",
    "Champions it: it directly advances the member's main priorities",
]


class SupportJudgeUnavailable(RuntimeError):
    """TypeSafe cannot be used: no API key, or the SDK is not installed."""


def stance_bar(persona: Persona) -> float:
    """Stance (0–3) a proposal needs before this member backs it.

    1.5 is "more favor than indifference". Cooperative members back sound
    work that merely doesn't hurt them; independent-minded ones want a clear
    win for their own priorities.
    """
    return min(2.0, max(1.0, 1.5 + (0.5 - persona.traits.cooperation)))


# ── Request ───────────────────────────────────────────────────────────


def _cap(text: str | None, limit: int = _TEXT_CAP) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def _is_supported(p: dict, supported_ids: set[str], my_user_id: str | None) -> bool:
    # Authors back their own proposal at creation. Counting authorship too
    # keeps a restarted agent (or a BotRunner turn, which starts with an
    # empty `supported_proposals`) from re-judging its own work.
    return p["id"] in supported_ids or (bool(my_user_id) and p.get("user_id") == my_user_id)


def _target_key(p: dict) -> tuple | None:
    """Identity of a target-named change, or None when it has no complete
    target (never collide on missing fields)."""
    ptype = p.get("proposal_type", "")
    if ptype == "ChangeVariable":
        name = (p.get("proposal_text") or "").strip().split("\n", 1)[0].strip()
        values = (name, str(p.get("val_text") or "").strip())
    elif ptype in _TARGET_FIELDS:
        values = tuple(str(p.get(f) or "").strip() for f in _TARGET_FIELDS[ptype])
    else:
        return None
    return (ptype, *values) if all(values) else None


def _rank(p: dict) -> tuple:
    """Order in which proposals claim an idea: on the pulse first, then the
    best supported, then the oldest. A later copy defers to an earlier one
    the member backs."""
    on_air = p.get("proposal_status") == "OnTheAir"
    return (0 if on_air else 1, -int(p.get("support_count") or 0), str(p.get("created_at") or ""))


def _proposal_state(p: dict, snapshot: CommunitySnapshot, users_cache: dict[str, str],
                    my_user_id: str | None) -> dict:
    ptype = p.get("proposal_type", "")
    val_uuid = str(p.get("val_uuid") or "")
    val_text = (p.get("val_text") or "").strip()
    s: dict = {"kind": ptype, "what_it_does": _WHAT_IT_DOES.get(ptype, ptype)}

    if ptype == "EditArtifact":
        title = next(
            (a.get("title") for arts in snapshot.container_artifacts.values()
             for a in arts if a.get("id") == val_uuid and a.get("title")),
            None,
        )
        if title or val_text:
            s["section"] = title or val_text
        s["current_text"] = _cap(p.get("_old_content")) or "(empty)"
        s["proposed_text"] = _cap(p.get("proposal_text")) or "(empty)"
    elif ptype == "ReplaceStatement":
        old = next((st["statement_text"] for st in snapshot.statements if st.get("id") == val_uuid), None)
        if old:
            s["current_text"] = _cap(old)
        s["proposed_text"] = _cap(val_text)
        if p.get("proposal_text"):
            s["text"] = _cap(p["proposal_text"])
    elif ptype == "CreateArtifact":
        s["section_title"] = val_text or _cap(p.get("proposal_text"), 200)
        if p.get("proposal_text") and p["proposal_text"].strip() != s["section_title"]:
            s["text"] = _cap(p["proposal_text"])
    else:
        s["text"] = _cap(p.get("proposal_text"))

    if ptype == "Membership" and val_uuid:
        # Applicants apply for themselves and are not members yet, so they
        # are not in users_cache; the proposal's author name is theirs.
        own = p.get("user_id") == val_uuid and p.get("user_name")
        s["applicant"] = users_cache.get(val_uuid) or own or "an applicant"
    elif ptype == "ThrowOut" and val_uuid:
        s["target_member"] = users_cache.get(val_uuid, "a member")
    elif ptype == "RemoveStatement":
        old = next((st["statement_text"] for st in snapshot.statements if st.get("id") == val_uuid), None)
        if old:
            s["rule_to_remove"] = _cap(old)
    elif p.get("_display"):
        s["target"] = p["_display"]

    if ptype == "ChangeVariable":
        name = (p.get("proposal_text") or "").strip().split("\n", 1)[0].strip()
        if name in snapshot.variables:
            s["current_value"] = str(snapshot.variables[name])
        if val_text:
            s["new_value"] = val_text

    if p.get("pitch"):
        s["authors_pitch"] = _cap(p["pitch"], 500)

    comments = [
        f'{users_cache.get(c.get("user_id", ""), "a member")}: {_cap(c.get("comment_text"), _COMMENT_CAP)}'
        for c in snapshot.proposal_comments.get(p["id"], [])
        if c.get("user_id") != my_user_id and c.get("comment_text")
    ][:3]
    if comments:
        s["comments"] = comments
    return s


def _proposal_questions(key: str, ptype: str, pstate: dict, has_plan: bool) -> dict:
    path = f"`proposals.{key}`"
    judge_by = "the member's background, decision style and current plan" if has_plan \
        else "the member's background and decision style"
    qs: dict = {
        f"stance:{key}": {
            "type": "score",
            "instructions": f"How much would `member` want {path} to be accepted, judging by {judge_by}?",
            "criteria": _STANCE_LEVELS,
        },
        f"harmful:{key}": {
            "type": "noul",
            "instructions": (
                f"Would accepting {path} damage the community, for example by removing "
                "a member who broke no rule, or by giving one member control over everyone?"
            ),
            "criteria": {
                "true": "Clear damage to members or to how the community governs itself",
                "false": "An ordinary change, even one some members would dislike",
            },
        },
    }
    if ptype == "CreateArtifact":
        # A new section is only its title, and the failure mode is a slogan
        # posing as one, so ask about the title directly.
        qs[f"concrete:{key}"] = {
            "type": "noul",
            "instructions": f"Does `proposals.{key}.section_title` name a specific part of the deliverable, rather than a slogan or theme?",
            "criteria": {
                "true": "A concrete section a reader could look up, such as 'Conflict Resolution Process' or 'How We Share Resources'",
                "false": "A slogan or theme, such as 'Commitment to Excellence' or 'Building a Better Community'",
            },
        }
    elif ptype in _TEXT_TYPES:
        qs[f"concrete:{key}"] = {
            "type": "noul",
            "instructions": f"Does {path} spell out a specific change, so members know exactly what accepting it does?",
            "criteria": {
                "true": "Names the concrete rule, value, section content or group involved",
                "false": "Generic wording such as 'be respectful' or 'ensure quality' that commits to nothing specific",
            },
        }
    if "current_text" in pstate:
        qs[f"improves:{key}"] = {
            "type": "noul",
            "instructions": f"Is `proposals.{key}.proposed_text` an improvement over `proposals.{key}.current_text`?",
            "criteria": {
                "true": "Adds real content to an empty section, or is more specific, complete or clear while keeping the useful detail",
                "false": "Drops useful detail, swaps specifics for generic statements, or is no better",
            },
        }
    if "comments" in pstate:
        qs[f"objection:{key}"] = {
            "type": "noul",
            "instructions": (
                f"Does a comment in `proposals.{key}.comments` point out a specific flaw in what "
                f"{path} actually says: something wrong, missing or harmful?"
            ),
            "criteria": {
                "true": "Names a concrete problem that matches the proposal's content",
                "false": "Only praise, a preference, a question, or a complaint that does not match what the proposal says",
            },
        }
    return qs


def _duplicate_question(key: str, other: str) -> dict:
    # One pair per question: the vote needs to know WHICH proposal this one
    # repeats, because only a copy of something the member backs is a
    # reason to decline (the better version of an idea is not a duplicate
    # of a vague one the member turned down).
    return {
        "type": "noul",
        "instructions": f"Does `proposals.{key}` ask for essentially the same change as `proposals.{other}`?",
        "criteria": {
            "true": "Same change with the same effect, even if worded differently",
            "false": "A different change, or a related change with a different effect",
        },
    }


_CHAT_QUESTIONS = {
    "chat:wait": {
        "type": "noul",
        "instructions": "Do the messages in `recent_chat` ask members to hold off on supporting the pulse for now?",
    },
    "chat:pulse": {
        "type": "noul",
        "instructions": "Do the messages in `recent_chat` ask members to support the pulse now?",
    },
}


# ── Cache ─────────────────────────────────────────────────────────────
# A proposal is re-judged every turn until the member backs it, and every
# member asks about the same proposals. Most answers don't depend on who is
# asking: whether a text is concrete, whether an edit improves its section,
# whether a change would harm the community, whether a comment names a real
# flaw, whether two proposals are the same change. Those are cached once for
# everyone, keyed by exactly what they read, so members also agree on them.
# Only stance (how much THIS member wants it) is per member, and it holds
# until the member's plan, the proposal, its comments or the rules change.


def _fingerprint(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class AnswerCache:
    """LRU of question answers, keyed by what each question reads."""

    def __init__(self, maxsize: int = 20_000):
        self._answers: OrderedDict[tuple, float] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: tuple) -> float | None:
        value = self._answers.get(key)
        if value is not None:
            self._answers.move_to_end(key)
        return value

    def put(self, key: tuple, value: float) -> None:
        self._answers[key] = value
        self._answers.move_to_end(key)
        if len(self._answers) > self._maxsize:
            self._answers.popitem(last=False)


def _cache_key(qid: str, fp: dict[str, str], member_fp: str, context_fp: str) -> tuple | None:
    """What a question reads, as a cache key; None for uncacheable (chat)."""
    kind, key, *other = qid.split(":")
    if kind == "stance":
        return (kind, member_fp, context_fp, fp[key], fp[f"{key}#comments"])
    if kind == "harmful":
        return (kind, context_fp, fp[key])
    if kind in ("concrete", "improves"):
        return (kind, fp[key])
    if kind == "objection":
        return (kind, fp[key], fp[f"{key}#comments"])
    if kind == "duplicate":
        return (kind, fp[key], fp[other[0]])
    return None


@dataclass
class VoteRequest:
    state: dict
    questions: dict               # asked this turn
    candidates: dict[str, dict]   # state key ("p3") → proposal, in rank order; ids never reach the model
    backed_keys: set[str] = field(default_factory=set)       # proposals the member already backs
    same_target: dict[str, list[str]] = field(default_factory=dict)  # candidate → earlier keys with its target
    cached: dict[str, float] = field(default_factory=dict)       # question → answer reused from the cache
    cache_keys: dict[str, tuple] = field(default_factory=dict)   # asked question → where to store its answer


def build_request(
    *,
    persona: Persona,
    intention: str,
    snapshot: CommunitySnapshot,
    supported_ids: set[str],
    users_cache: dict[str, str],
    my_user_id: str | None,
    cache: AnswerCache | None = None,
) -> VoteRequest:
    open_props = sorted(snapshot.proposals_on_the_air + snapshot.proposals_out_there, key=_rank)
    backed = [p for p in open_props if _is_supported(p, supported_ids, my_user_id)]
    pending = [p for p in open_props if not _is_supported(p, supported_ids, my_user_id)]
    pending = pending[:MAX_PROPOSALS_PER_TURN]

    member = {
        "name": persona.name,
        "role": persona.role,
        "background": persona.background.strip(),
        "decision_style": persona.decision_style.strip(),
        "temperament": persona.trait_summary(),
    }
    if intention:
        member["current_plan"] = intention
    community: dict = {"name": snapshot.community_name}
    mission = next((c.get("mission") for c in snapshot.containers if c.get("mission")), None)
    if mission:
        community["mission"] = _cap(mission)
    if snapshot.statements:
        community["rules"] = [_cap(s.get("statement_text"), 300) for s in snapshot.statements[:12]]
    state: dict = {"member": member, "community": community}
    req = VoteRequest(state=state, questions={}, candidates={})

    if pending:
        # Proposals the member already backs are candidates for comparison,
        # so a pending copy of one of them is recognised as a duplicate.
        proposals: dict = {}
        keys: dict[str, str] = {}
        for i, p in enumerate(backed + pending, start=1):
            keys[p["id"]] = f"p{i}"
            proposals[f"p{i}"] = _proposal_state(p, snapshot, users_cache, my_user_id)
        req.backed_keys = {keys[p["id"]] for p in backed}
        wanted: dict = {}
        earlier = list(backed)
        for p in pending:
            key, ptype = keys[p["id"]], p.get("proposal_type", "")
            req.candidates[key] = p
            wanted.update(_proposal_questions(key, ptype, proposals[key], bool(intention)))
            same_type = [e for e in earlier if e.get("proposal_type") == ptype]
            target = _target_key(p)
            if target is not None:
                matches = [keys[e["id"]] for e in same_type if _target_key(e) == target]
                if matches:
                    req.same_target[key] = matches
            elif ptype in _SEMANTIC_DUPLICATE_TYPES:
                for e in same_type:
                    wanted[f"duplicate:{key}:{keys[e['id']]}"] = _duplicate_question(key, keys[e["id"]])
            earlier.append(p)

        fp: dict[str, str] = {}
        for k, pstate in proposals.items():
            fp[k] = _fingerprint({f: v for f, v in pstate.items() if f != "comments"})
            fp[f"{k}#comments"] = _fingerprint(pstate.get("comments", []))
        member_fp, context_fp = _fingerprint(member), _fingerprint(community)
        referenced: set[str] = set()
        for qid, question in wanted.items():
            ckey = _cache_key(qid, fp, member_fp, context_fp)
            hit = cache.get(ckey) if cache is not None and ckey is not None else None
            if hit is not None:
                req.cached[qid] = hit
                continue
            req.questions[qid] = question
            if ckey is not None:
                req.cache_keys[qid] = ckey
            referenced.update(qid.split(":")[1:])
        # Only proposals a question still reads go to the model: less to
        # pay for, and less irrelevant state to distract the judgment.
        if referenced:
            state["proposals"] = {k: v for k, v in proposals.items() if k in referenced}

    if snapshot.chat_messages and open_props:
        state["recent_chat"] = [
            f'{users_cache.get(m.get("user_id", ""), "a member")}: {_cap(m.get("comment_text"), _COMMENT_CAP)}'
            for m in reversed(snapshot.chat_messages[:_CHAT_MESSAGES])
            if m.get("comment_text")
        ]
        if state["recent_chat"]:
            req.questions.update(_CHAT_QUESTIONS)

    return req


# ── Decisions ─────────────────────────────────────────────────────────


@dataclass
class ProposalJudgment:
    stance: float
    harmful: float
    concrete: float | None = None
    improves: float | None = None
    duplicate: float | None = None
    objection: float | None = None


def decide_proposal(persona: Persona, ptype: str, j: ProposalJudgment) -> tuple[bool, str]:
    """Turn one proposal's judgments into (back it?, first-person reason)."""
    if ptype in _TEXT_TYPES and j.concrete is not None and j.concrete < CONCRETE_MIN:
        return False, "Too vague to commit to"
    if j.improves is not None and j.improves < IMPROVES_MIN:
        return False, "Worse than the text it replaces"
    if j.duplicate is not None and j.duplicate > DUPLICATE_MAX:
        return False, "Repeats a proposal I already back"
    if j.harmful > HARM_MAX:
        return False, "Would damage the community"

    bar = stance_bar(persona)
    objected = j.objection is not None and j.objection >= OBJECTION_MIN
    if j.stance < bar + (OBJECTION_BAR if objected else 0.0):
        if j.stance < 1.0:
            return False, "Works against what I want"
        if objected and j.stance >= bar:
            return False, "Not convinced: a comment points out a real flaw"
        return False, "Not enough in it for my priorities"
    if j.stance >= 2.5:
        return True, "Directly advances my priorities"
    if j.stance >= 1.5:
        return True, "Helps something I care about"
    return True, "Sound, and nothing in it works against me"


def _decide_threshold(p: dict, snapshot: CommunitySnapshot) -> int:
    t = p.get("decide_threshold")
    return int(t) if t else max(1, snapshot._threshold_for_type(p.get("proposal_type", "")))


def _promote_threshold(p: dict, snapshot: CommunitySnapshot) -> int:
    t = p.get("promote_threshold")
    return int(t) if t else max(1, snapshot._proposal_support_threshold())


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def decide_pulse(
    persona: Persona,
    snapshot: CommunitySnapshot,
    *,
    backs: set[str],
    newly_backed: set[str],
    already_supported: bool,
    chat_wait: bool = False,
    chat_pulse: bool = False,
) -> tuple[bool, str]:
    """Should this member support the next pulse now?

    Scores what firing the pulse NOW would do to each open proposal, from
    this member's side, mirroring PulseService: OnTheAir proposals are
    accepted at `support >= decide_threshold`, otherwise rejected; OutThere
    proposals already at MaxAge are canceled (before promotion is even
    checked), otherwise promoted at `support >= promote_threshold`.

    `backs` is every open proposal the member supports, including the ones
    it is backing this turn (`newly_backed`), whose support has not been
    counted by the server yet.
    """
    open_props = snapshot.proposals_on_the_air + snapshot.proposals_out_there
    if not open_props:
        return False, "No open proposals: a pulse would decide nothing"
    if already_supported:
        return False, "Already supporting this pulse"

    try:
        max_age = int(float(snapshot.variables.get("MaxAge", 2)))
    except (TypeError, ValueError):
        max_age = 2
    passes = rejects = sinks = lets_through = promotes = kills = loses = 0
    for p in snapshot.proposals_on_the_air:
        mine = p["id"] in backs
        support = int(p.get("support_count") or 0) + (1 if p["id"] in newly_backed else 0)
        if support >= _decide_threshold(p, snapshot):
            if mine:
                passes += 1
            else:
                lets_through += 1
        elif mine:
            sinks += 1
        else:
            rejects += 1
    for p in snapshot.proposals_out_there:
        mine = p["id"] in backs
        support = int(p.get("support_count") or 0) + (1 if p["id"] in newly_backed else 0)
        if int(p.get("age") or 0) >= max_age:
            if mine:
                loses += 1
            else:
                kills += 1
        elif mine and support >= _promote_threshold(p, snapshot):
            promotes += 1

    gains = passes + rejects + promotes + kills
    net = gains - (sinks + lets_through + loses)

    # #217: with only one or two proposals open, pulsing resolves them before
    # the rest of the community has had a turn to weigh in, so the pulse has
    # to be clearly worth it. A busy board can take a verdict.
    required = 1 if len(open_props) <= 2 else 0
    if persona.traits.patience < 0.4:
        required -= 1
    elif persona.traits.patience > 0.7:
        required += 1
    if chat_wait:
        required += 1
    if chat_pulse:
        required -= 1
    required = max(0, required)

    good = [s for s in (
        passes and f"pass {_plural(passes, 'proposal')} I back",
        rejects and f"reject {rejects} I don't",
        promotes and f"put {promotes} I back on the next pulse",
        kills and f"retire {kills} stale one{'s' if kills != 1 else ''} I don't back",
    ) if s]
    bad = [s for s in (
        sinks and f"sink {_plural(sinks, 'proposal')} I back that still {'lacks' if sinks == 1 else 'lack'} support",
        lets_through and f"pass {lets_through} I don't back",
        loses and f"cancel {loses} I back that {'is' if loses == 1 else 'are'} aging out",
    ) if s]

    if gains and net >= required:
        why = "Pulse now: it would " + ", ".join(good)
        if bad:
            why += ", even though it would also " + " and ".join(bad)
        if chat_pulse:
            why += " (and chat is asking for it)"
        return True, why
    if bad and net < required:
        return False, "Holding the pulse: it would " + " and ".join(bad)
    if chat_wait:
        return False, "Holding the pulse: chat asked to wait"
    if not gains:
        return False, "Holding the pulse: firing now would settle nothing I care about"
    return False, f"Holding the pulse: only {_plural(len(open_props), 'proposal')} open, letting them gather support"


# ── Votes ─────────────────────────────────────────────────────────────


@dataclass
class ProposalVote:
    proposal: dict
    support: bool
    reason: str
    judgment: ProposalJudgment


@dataclass
class SupportVotes:
    proposals: list[ProposalVote] = field(default_factory=list)
    pulse: bool = False
    pulse_reason: str = ""
    model: str = ""
    # Raw chat signals, when chat was read (None = no chat this turn).
    chat_wait: float | None = None
    chat_pulse: float | None = None
    # What the turn cost: questions sent vs reused, and billed input tokens.
    asked: int = 0
    cached: int = 0
    input_tokens: int = 0

    def actions(self) -> list[AgentAction]:
        acts = [
            AgentAction(
                action_type="support_proposal",
                reason=v.reason,
                params={"proposal_id": v.proposal["id"]},
                eager_front="support",
            )
            for v in self.proposals if v.support
        ]
        if self.pulse:
            acts.append(AgentAction(action_type="support_pulse", reason=self.pulse_reason, eager_front="pulse"))
        return acts

    def prompt_lines(self) -> list[str]:
        """How the member voted, for the LLM prompt. Ids are tagged so the
        LLM can comment on a proposal it declined."""
        lines = []
        for v in self.proposals:
            p = v.proposal
            text = (p.get("_display") or p.get("proposal_text") or "").strip().replace("\n", " ")[:60]
            mark = "✓ backing" if v.support else "✗ declining"
            lines.append(
                f'{mark} [{p.get("proposal_type")}] "{text}" '
                f'id={tag_id("proposal", p["id"])} — {v.reason}'
            )
        lines.append(f"pulse: {'supporting' if self.pulse else 'not supporting'} — {self.pulse_reason}")
        return lines


def _judgment(answers: dict[str, float], req: VoteRequest, key: str, backed_keys: set[str]) -> ProposalJudgment:
    # How surely this repeats a proposal the member backs (so far this turn
    # included): 1.0 for a same-target match, the model's yes otherwise.
    copies = [1.0 for other in req.same_target.get(key, []) if other in backed_keys]
    copies += [
        value for qid, value in answers.items()
        if qid.startswith(f"duplicate:{key}:") and qid.rsplit(":", 1)[1] in backed_keys
    ]
    return ProposalJudgment(
        stance=answers[f"stance:{key}"],
        harmful=answers[f"harmful:{key}"],
        concrete=answers.get(f"concrete:{key}"),
        improves=answers.get(f"improves:{key}"),
        duplicate=max(copies) if copies else None,
        objection=answers.get(f"objection:{key}"),
    )


class SupportJudge:
    """Decides an agent's support votes with at most one TypeSafe request per
    turn, and none when every answer it needs is already cached."""

    def __init__(self, client, model: str = TYPESAFE_MODEL):
        self._client = client
        self.model = model
        self._cache = AnswerCache()
        self._call_count = 0
        self._error_count = 0
        self._asked = 0
        self._cached = 0

    @classmethod
    def from_config(cls) -> SupportJudge:
        """Build a judge from TYPESAFE_API_KEY or config.ini [typesafe] api_key
        (the same env-then-config.ini lookup the OpenRouter key uses)."""
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            cfg = configparser.ConfigParser()
            cfg.read("config.ini")
            api_key = cfg.get("typesafe", "api_key", fallback=None)
        if not api_key:
            raise SupportJudgeUnavailable(
                "no TypeSafe API key: set TYPESAFE_API_KEY or config.ini [typesafe] api_key"
            )
        try:
            from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        except ImportError as e:
            raise SupportJudgeUnavailable(
                "typesafe-sdk is not installed: pip install -e '.[agents]'"
            ) from e
        # The SDK already retries 408/429/5xx (529 "overloaded" included)
        # with backoff; cap the whole call so a TypeSafe outage costs a turn
        # a bounded wait before the agent falls back to the LLM.
        client = AsyncTypeSafeClient(
            api_key=api_key, model=TYPESAFE_MODEL, retry=RetryPolicy(timeout=20.0),
        )
        return cls(client)

    @property
    def stats(self) -> dict:
        return {
            "model": self.model, "calls": self._call_count, "errors": self._error_count,
            "questions_asked": self._asked, "questions_cached": self._cached,
        }

    async def vote(
        self,
        *,
        persona: Persona,
        intention: str,
        snapshot: CommunitySnapshot,
        supported_proposals: set[str],
        supported_pulse_ids: set[str],
        users_cache: dict[str, str],
        my_user_id: str | None,
    ) -> SupportVotes:
        req = build_request(
            persona=persona, intention=intention, snapshot=snapshot,
            supported_ids=supported_proposals, users_cache=users_cache, my_user_id=my_user_id,
            cache=self._cache,
        )
        votes = SupportVotes(model=self.model, asked=len(req.questions), cached=len(req.cached))
        answers = dict(req.cached)
        if req.questions:
            try:
                response = await self._client.system_one(req.state, req.questions, model=self.model)
            except Exception:
                self._error_count += 1
                raise
            self._call_count += 1
            votes.model = response.model
            votes.input_tokens = getattr(getattr(response, "usage", None), "input_tokens", 0) or 0
            for qid in req.questions:
                value = response.scores[qid].score if qid.startswith("stance:") else response.nouls[qid].noul
                answers[qid] = value
                if qid in req.cache_keys:
                    self._cache.put(req.cache_keys[qid], value)
        self._asked += votes.asked
        self._cached += votes.cached

        # Rank order: a proposal backed earlier in this loop is one its later
        # copies defer to.
        backed_keys = set(req.backed_keys)
        for key, p in req.candidates.items():
            j = _judgment(answers, req, key, backed_keys)
            support, reason = decide_proposal(persona, p.get("proposal_type", ""), j)
            if support:
                backed_keys.add(key)
            votes.proposals.append(ProposalVote(proposal=p, support=support, reason=reason, judgment=j))

        newly_backed = {v.proposal["id"] for v in votes.proposals if v.support}
        backs = {
            p["id"] for p in snapshot.proposals_on_the_air + snapshot.proposals_out_there
            if _is_supported(p, supported_proposals, my_user_id)
        } | newly_backed
        if "chat:wait" in answers:
            votes.chat_wait = answers["chat:wait"]
            votes.chat_pulse = answers["chat:pulse"]
        next_pulse = snapshot.next_pulse
        votes.pulse, votes.pulse_reason = decide_pulse(
            persona, snapshot,
            backs=backs, newly_backed=newly_backed,
            already_supported=bool(next_pulse and next_pulse["id"] in supported_pulse_ids),
            chat_wait=(votes.chat_wait or 0.0) >= CHAT_SIGNAL_MIN,
            chat_pulse=(votes.chat_pulse or 0.0) >= CHAT_SIGNAL_MIN,
        )
        return votes

    async def aclose(self) -> None:
        await self._client.aclose()


def make_support_judge(mode: str) -> SupportJudge | None:
    """Resolve the --support-judge mode.

    auto      TypeSafe when an API key and the SDK are available, else the LLM
    typesafe  TypeSafe, and fail loudly at startup if it is unavailable
    llm       the LLM decides support votes inside its turn, as before
    """
    mode = (mode or "auto").strip().lower()
    if mode not in MODES:
        raise ValueError(f"unknown support judge {mode!r}; expected one of {', '.join(MODES)}")
    if mode == "llm":
        logger.info("Support votes: decided by the LLM")
        return None
    try:
        judge = SupportJudge.from_config()
    except SupportJudgeUnavailable as e:
        if mode == "typesafe":
            raise
        logger.warning("Support votes: decided by the LLM (TypeSafe unavailable — %s)", e)
        return None
    logger.info("Support votes: decided by TypeSafe %s", judge.model)
    return judge
