#!/usr/bin/env python3
"""Pick the simulation's model by measurement, not by price list.

Each candidate gets exactly what a live agent turn sends: the prompt from
`build_decision_prompt`, the request body from `openrouter_payload`, and
its reply goes through the simulation's own parser
(`DecisionEngine._parse_response`). Four scenarios:

  live   The CURRENT state of every running community, read from the
         public API and rendered by the agents' own code
         (`observe_community` + `summarize`): real ids, real missions,
         real clutter. A model that invents ids fails here first.
  busy   Three open proposals and an empty artifact. Does it act?
  judge  Two clearly good and two clearly bad proposals. A proposal only
         FAILS when enough members withhold support, so what matters is
         how rarely the bad ones get backed — under the 25% threshold.
  empty  Nothing to support. Does it start work, or idle?

Cost is OpenRouter's own accounting per call (reasoning tokens included),
not the headline price — a reasoning-by-default model can bill several
times its list price.

    .venv/bin/python scripts/llm_bakeoff.py                 # default shortlist
    .venv/bin/python scripts/llm_bakeoff.py --models openai/gpt-oss-20b --runs 2
    .venv/bin/python scripts/llm_bakeoff.py --json results.json

Read-only toward the site (GETs only). Spends OpenRouter credit: ~31 calls
per model per --runs, each a few thousand input tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import configparser
import dataclasses
import json
import logging
import os
import re
import statistics
import sys
import time
import uuid

import httpx

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from agents.api_client import KBZClient
from agents.community_state import observe_community, strip_id_tag, tag_id
from agents.decision_engine import (
    DecisionEngine,
    build_decision_prompt,
    openrouter_payload,
)
from agents.persona import load_all_personas

OPENROUTER = "https://openrouter.ai/api/v1"
SITE_API = "https://kibbutznik.org/kbz"

# Cheap enough to run the demo on, plus the previous default as the
# known-good reference. Edit freely — the catalog changes monthly.
DEFAULT_MODELS = [
    "openai/gpt-oss-20b",
    "qwen/qwen3.7-flash",
    "deepseek/deepseek-v4.1-flash-1017",
    "z-ai/glm-5.3-flash",
    "google/gemma-4-26b-a4b-it",
    "nvidia/nemotron-3.5-lightning:free",
    "mistralai/mistral-small-3.2-24b-instruct",
    "anthropic/claude-haiku-5.5",
    "mistralai/mistral-small-2603",
]

_PARSER = DecisionEngine.__new__(DecisionEngine)  # _parse_response needs no state
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


# ── Scenarios ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class Scenario:
    kind: str                     # live | busy | judge | empty
    label: str
    prompt: str
    known: frozenset[str]         # full lowercase UUIDs of everything the prompt shows
    good: tuple[str, ...] = ()    # judge only
    bad: tuple[str, ...] = ()
    repeat: int = 1


_NS = uuid.UUID("0b5e7a1c-3f2d-4e6b-9a8c-7d1e2f3a4b5c")


def _id(name: str) -> str:
    """Deterministic UUID for a synthetic entity, stable across runs."""
    return str(uuid.uuid5(_NS, name))


def _queue_line(ptype: str, text: str, full_id: str) -> str:
    """The judgment-queue line, in the exact shape agent.py builds it."""
    return f'[{ptype}] "{text[:60]}" id={full_id[:8]}'


PERSONAS = [
    {"name": "Yael Bar-On", "me": "yael",
     "background": "Agronomist, twelve years on a communal farm. Distrusts vague rules.",
     "style": "pragmatic; backs concrete proposals, ignores rhetoric",
     "intention": "Get the onboarding procedure written down in concrete terms."},
    {"name": "Dov Aluf", "me": "dov",
     "background": "Archivist. Cares about process and pace.",
     "style": "procedural; guards deliberation time",
     "intention": "Stop proposals aging out before people can read them."},
    {"name": "Noa Shalev", "me": "noa",
     "background": "Builder. Impatient with ceremony.",
     "style": "wants shipped artifacts, not slogans",
     "intention": "Fill the empty handbook artifacts with real content."},
]

# The synthetic community every non-live scenario shares.
S1, S2 = _id("stmt/1"), _id("stmt/2")
C_ROOT, A_PLAN, A_ONB = _id("container/root"), _id("artifact/plan"), _id("artifact/onboarding")
K_WRITERS = _id("action/writers")
MEMBERS = [(n, _id(f"user/{n}")) for n in
           ("yael", "dov", "noa", "eitan", "maya", "omer", "tamar", "gil", "ruth", "amit")]
BASE_KNOWN = {S1, S2, C_ROOT, A_PLAN, A_ONB, K_WRITERS} | {u for _, u in MEMBERS}


def _summary(me: str, out_there=(), on_air=()) -> str:
    """A community state in the same layout `CommunitySnapshot.summarize` renders."""
    t = tag_id
    lines = [
        "## Community: The Handbook Kibbutz",
        f"Members: {len(MEMBERS)}",
        "Pulse progress: 2/4",
        "",
        "### Community Rules / Disclaimer (2 statements):",
        f"  1. [id={t('statement', S1)}] Every proposal names the artifact or rule it changes.",
        f"  2. [id={t('statement', S2)}] Members answer direct comments on their proposals within one pulse.",
        "",
        "### ★ Artifact Containers (1) — what this community is producing:",
        f'  Container "Root" [id={t("container", C_ROOT)}] — OPEN (root)',
        "    >>> MISSION: Ship a governance handbook that a newcomer can follow on day one.",
        f"    📋 PLAN [{t('artifact', A_PLAN)}]:",
        "      Sections: 1. Onboarding  2. Proposals and pulses  3. Resolving conflict",
        f"      → To update the plan: EditArtifact val_uuid={t('artifact', A_PLAN)}",
        f'    [{t("artifact", A_ONB)}] "Onboarding" by dov — current content:',
        "      (empty)",
        f"      → To improve: EditArtifact val_uuid={t('artifact', A_ONB)}",
        f"    → To commit: CommitArtifact with val_uuid={t('container', C_ROOT)} and val_text=JSON list of artifact ids in chosen order",
        "",
        "### Active Actions (1):",
        (f"  - [Handbook Writers] id={t('action', K_WRITERS)} (3 members, 2 pulses fired, "
         "0 active proposals, 1 accepted / 0 rejected)"),
        "",
    ]
    if out_there:
        lines.append(f"### Proposals Gathering Support — need 3 to reach OnTheAir ({len(out_there)}):")
        for ptype, text, author, support, pid in out_there:
            lines.append(
                f'  - [{ptype}] "{text}" by {author} | support: {support}/10 | '
                f"needs {max(0, 3 - support)} more to promote | age 0/2 | id: {t('proposal', pid)}")
        lines.append("")
    if on_air:
        lines.append("### Proposals Being Decided (if pulse fires NOW):")
        for ptype, text, author, support, pid in on_air:
            lines.append(
                f'  - [{ptype}] "{text}" by {author} | support: {support}/5 needed '
                f"→ **WILL PASS** | id: {t('proposal', pid)}")
        lines.append("")
    lines.append(f"### Members ({len(MEMBERS)}):")
    for i, (name, uid) in enumerate(MEMBERS):
        you = " ← you" if name == me else ""
        lines.append(f"  - {name} (user_id={t('user', uid)}, seniority={20 - 2 * i}){you}")
    lines += [
        "",
        "### Key Variables: PulseSupport=40, ProposalSupport=25, Membership=50, ThrowOut=60, MaxAge=2",
        "",
        "### My Status: seniority=18",
    ]
    return "\n".join(lines)


def _prompt(persona: dict, summary: str, *, queue=(), supported=(), initiative=0.5,
            active=0, memory="", history=()) -> str:
    return build_decision_prompt(
        persona_name=persona["name"], persona_role="member",
        persona_background=persona["background"],
        persona_decision_style=persona["style"],
        persona_communication_style="terse, direct",
        persona_trait_summary="conscientious, moderately agreeable",
        community_summary=summary, action_history=list(history),
        unsupported_proposals=list(queue), already_supported_proposals=list(supported),
        already_commented=[], consecutive_do_nothings=0, initiative=initiative,
        total_active_proposals=active, interview_context="",
        memory_context=memory, recent_failures=[],
    )


def _intention(p: dict) -> str:
    return f"CURRENT INTENTION (your one-line plan from last turn):\n→ {p['intention']}"


def busy_scenarios() -> list[Scenario]:
    p_onb, p_var, p_edit = _id("busy/onboarding"), _id("busy/pulse"), _id("busy/edit")
    out_there = [
        ("AddStatement", "New members get a named buddy who answers their questions for their first two pulses.",
         "maya", 1, p_onb),
        ("ChangeVariable", "PulseSupport\\nLower to 30 — pulses stall waiting for the last few supporters.",
         "omer", 2, p_var),
    ]
    on_air = [("EditArtifact", "Onboarding: 1. Read the rules. 2. Meet your buddy. 3. Support one proposal.",
               "dov", 3, p_edit)]
    p = PERSONAS[0]
    prompt = _prompt(
        p, _summary(p["me"], out_there, on_air),
        queue=[_queue_line(x[0], x[1], x[4]) for x in out_there],
        supported=[_queue_line(x[0], x[1], x[4]) for x in on_air],
        initiative=0.7, active=3, memory=_intention(p),
        history=["[10:02] support_proposal: supported the onboarding EditArtifact"])
    return [Scenario("busy", "busy-board", prompt,
                     frozenset(BASE_KNOWN | {p_onb, p_var, p_edit}), repeat=4)]


def judge_scenarios() -> list[Scenario]:
    good1, good2 = _id("judge/good-pairing"), _id("judge/good-maxage")
    bad1, bad2 = _id("judge/bad-platitude"), _id("judge/bad-duplicate")
    out_there = [
        ("AddStatement", ("New members are paired with an existing member for their first three pulses; "
                          "the pair reviews one open proposal together each pulse."), "eitan", 1, good1),
        ("ChangeVariable", ("MaxAge\\nRaise from 2 to 4 — proposals are aging out before members "
                            "on slower schedules get a turn to read them."), "ruth", 1, good2),
        ("EditArtifact", ("## Our Values\\n\\nWe value excellence, quality, and continuous improvement "
                          "in everything we do. Together we will strive for the best outcomes."), "gil", 0, bad1),
        ("AddStatement", ("New members should be paired with an existing member for their first "
                          "three pulses."), "amit", 0, bad2),
    ]
    queue = [_queue_line(x[0], x[1], x[4]) for x in out_there]
    known = frozenset(BASE_KNOWN | {good1, good2, bad1, bad2})
    return [
        Scenario("judge", f"judge/{p['me']}",
                 _prompt(p, _summary(p["me"], out_there), queue=queue, active=4,
                         memory=_intention(p),
                         history=["[09:40] comment: asked how the handbook sections are split"]),
                 known, good=(good1, good2), bad=(bad1, bad2), repeat=3)
        for p in PERSONAS
    ]


def empty_scenarios() -> list[Scenario]:
    p = PERSONAS[0]
    return [
        Scenario("empty", f"empty/initiative={init}",
                 _prompt(p, _summary(p["me"]), initiative=init, active=0,
                         memory="You care about onboarding being written down.",
                         history=["[09:55] do_nothing: nothing to support"]),
                 frozenset(BASE_KNOWN), repeat=2)
        for init in (0.3, 0.5, 0.8)
    ]


async def live_scenarios(site: str, per_community: int) -> list[Scenario]:
    """Render each running community exactly as its agents see it."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.get(f"{site}/simulation/communities")
        r.raise_for_status()
        communities = r.json()["communities"]
    except Exception as e:  # noqa: BLE001 — the synthetic scenarios still run
        print(f"  live scenarios skipped — {site} unreachable: {e}")
        return []
    personas = sorted(load_all_personas(), key=lambda p: p.name)
    client = KBZClient(site)
    out: list[Scenario] = []
    try:
        for c in communities:
            snap = await observe_community(client, c["community_id"])
            users: dict[str, str] = {}
            for m in snap.members:
                try:
                    users[m["user_id"]] = (await client.get_user(m["user_id"]))["user_name"]
                except Exception:  # noqa: BLE001
                    users[m["user_id"]] = m["user_id"][:8]
            known = frozenset(u.lower() for u in _UUID_RE.findall(
                json.dumps(dataclasses.asdict(snap), default=str)))
            active = snap.proposals_out_there + snap.proposals_on_the_air
            queue = [_queue_line(p["proposal_type"], p["proposal_text"], p["id"]) for p in active]
            members = sorted(snap.members, key=lambda m: users[m["user_id"]])
            founders = [m for m in members if "_applicant" not in users[m["user_id"]]]
            for m in (founders or members)[:per_community]:
                persona = personas[len(out) % len(personas)]
                name = users[m["user_id"]]
                summary = snap.summarize(my_user_id=m["user_id"], users_cache=users,
                                         supported_proposals=set())
                prompt = build_decision_prompt(
                    persona_name=name, persona_role=persona.role,
                    persona_background=persona.background,
                    persona_decision_style=persona.decision_style,
                    persona_communication_style=persona.communication_style,
                    persona_trait_summary=persona.trait_summary(),
                    community_summary=summary, action_history=[],
                    unsupported_proposals=queue, already_supported_proposals=[],
                    already_commented=[], consecutive_do_nothings=0,
                    initiative=persona.traits.initiative,
                    total_active_proposals=len(active), interview_context="",
                    memory_context="", recent_failures=[],
                )
                out.append(Scenario("live", f"{c['slug']}/{name}", prompt, known, repeat=2))
    finally:
        await client.close()
    return out


# ── Calling and scoring ───────────────────────────────────────────────

@dataclasses.dataclass
class Call:
    model: str
    scenario: Scenario
    ok: bool = False
    error: str = ""
    latency: float = 0.0
    cost: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    finish: str = ""
    provider: str = ""
    raw_names: list = dataclasses.field(default_factory=list)
    actions: list = dataclasses.field(default_factory=list)


def _raw_action_names(text: str) -> list[str]:
    """Action names exactly as emitted, before the parser coerces them."""
    try:
        start, end = text.find("["), text.rfind("]") + 1
        return [o.get("action", "?") for o in json.loads(text[start:end]) if isinstance(o, dict)]
    except Exception:  # noqa: BLE001
        return ["<unparseable>"]


async def call_model(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                     model: str, sc: Scenario, reasoning: dict, extra: dict) -> Call:
    rec = Call(model, sc)
    body = openrouter_payload(model, sc.prompt)
    body["reasoning"] = reasoning
    body.update(extra)
    body["usage"] = {"include": True}  # OpenRouter's own cost accounting
    data = None
    async with sem:
        for attempt in range(4):
            t0 = time.monotonic()
            try:
                r = await client.post("/chat/completions", json=body)
            except Exception as e:  # noqa: BLE001 — TLS resets surface as bare ssl.SSLError
                if attempt < 3:
                    await asyncio.sleep(4 * 2 ** attempt)
                    continue
                rec.error = type(e).__name__
                return rec
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                await asyncio.sleep(4 * 2 ** attempt)
                continue
            if r.status_code >= 400:
                rec.error = f"HTTP {r.status_code}: {r.text[:140]}"
                return rec
            rec.latency = time.monotonic() - t0
            data = r.json()
            break
    if not data or "error" in data or not data.get("choices"):
        rec.error = f"bad response: {str(data)[:140]}"
        return rec
    choice = data["choices"][0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    rec.cost = usage.get("cost")
    rec.prompt_tokens = usage.get("prompt_tokens") or 0
    rec.completion_tokens = usage.get("completion_tokens") or 0
    rec.reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    rec.finish = choice.get("finish_reason") or ""
    rec.provider = data.get("provider") or ""
    # Same fallback `_call_openrouter` applies to an empty content field.
    text = msg.get("content") or msg.get("reasoning") or ""
    if not text:
        rec.error = f"empty content (finish_reason={rec.finish})"
        return rec
    rec.ok = True
    rec.raw_names = _raw_action_names(text)
    rec.actions = DecisionEngine._parse_response(_PARSER, text)
    return rec


async def probe_reasoning(client: httpx.AsyncClient, model: str) -> dict:
    """The simulation sends reasoning OFF. Some models refuse that outright
    (HTTP 400 "Reasoning is mandatory for this endpoint and cannot be
    disabled"), which in production means EVERY agent turn fails. Find the
    least reasoning such a model accepts, so it can still be judged — and
    so the report can say it needs an entry in
    decision_engine.OPENROUTER_REASONING_OVERRIDES."""
    prod = openrouter_payload(model, "Reply with [] only.")["reasoning"]
    for setting in (prod, {"effort": "low"}):
        body = {"model": model, "max_tokens": 300, "reasoning": setting,
                "messages": [{"role": "user", "content": "Reply with [] only."}]}
        try:
            r = await client.post("/chat/completions", json=body)
        except Exception:  # noqa: BLE001
            return prod
        if r.status_code != 400 or "mandatory" not in r.text.lower():
            return setting
    return {"effort": "low"}


def _resolve(value, known) -> str | None:
    """Mirror Agent._resolve_*: a full UUID passes through unchanged (so it
    must exist); anything shorter is prefix-matched on its hex runs."""
    body = strip_id_tag(str(value).strip()).lower()
    if len(body) >= 36 and _UUID_RE.fullmatch(body):
        return body if body in known else None
    for cand in re.findall(r"[0-9a-f]{6,}", body):
        for k in known:
            if k.startswith(cand):
                return k
    return None


def _id_refs(action) -> list[str]:
    p = action.params or {}
    refs = [p[k] for k in ("proposal_id", "comment_id", "val_uuid")
            if isinstance(p.get(k), str) and p.get(k).strip()]
    if action.action_type == "create_proposal" and p.get("proposal_type") in ("DelegateArtifact", "CommitArtifact"):
        refs += re.findall(r"[A-Za-z]-[0-9a-fA-F-]{6,}", str(p.get("val_text") or ""))
    return refs


def _pct(a: float, b: float) -> float | None:
    return (a / b * 100) if b else None


def score(calls: list[Call], price: tuple[float, float] | None) -> dict:
    ok = [c for c in calls if c.ok]
    s: dict = {"calls": len(calls), "ok": len(ok)}
    s["errors"] = collections.Counter(c.error.split(":")[0] for c in calls if not c.ok)
    s["parse_fail"] = sum(
        1 for c in ok if len(c.actions) == 1 and c.actions[0].action_type == "do_nothing"
        and "parse" in (c.actions[0].reason or "").lower())

    # Productivity: share of turns that did something other than do_nothing.
    work = [c for c in ok if c.scenario.kind in ("live", "busy", "empty")]
    s["active_turns"] = _pct(sum(any(a.action_type != "do_nothing" for a in c.actions) for c in work), len(work))
    s["mix"] = collections.Counter(a.action_type for c in work for a in c.actions)

    # Id fidelity: every id an action carries must resolve against state.
    with_ids = [(c, a) for c in ok for a in c.actions if _id_refs(a)]
    bad_ids = [(c, a, v) for c, a in with_ids for v in _id_refs(a) if _resolve(v, c.scenario.known) is None]
    s["invented"] = _pct(len({id(a) for _, a, _ in bad_ids}), len(with_ids))
    s["invented_examples"] = [f"{c.scenario.label}: {a.action_type} {v!r}" for c, a, v in bad_ids[:3]]
    s["illegal"] = collections.Counter(
        n for c in ok for n in c.raw_names if n not in DecisionEngine._LEGAL_ACTIONS)

    # Judgment: support for good vs bad proposals, per turn.
    judge = [c for c in ok if c.scenario.kind == "judge"]
    good = bad = objections = 0
    for c in judge:
        backed = {_resolve(a.params.get("proposal_id", ""), c.scenario.good + c.scenario.bad)
                  for a in c.actions if a.action_type == "support_proposal"}
        good += len(backed & set(c.scenario.good))
        bad += len(backed & set(c.scenario.bad))
        objections += sum(
            1 for a in c.actions if a.action_type == "comment"
            and _resolve(a.params.get("proposal_id", ""), c.scenario.bad))
    s["good"] = _pct(good, 2 * len(judge))
    s["bad"] = _pct(bad, 2 * len(judge))
    s["objections"] = objections

    # Empty board: idle turns are do_nothing or a pulse with nothing to decide.
    empty = [c for c in ok if c.scenario.kind == "empty"]
    s["wasted"] = _pct(sum(all(a.action_type in ("do_nothing", "support_pulse") for a in c.actions)
                           for c in empty), len(empty))

    lat = sorted(c.latency for c in ok)
    s["lat_med"] = statistics.median(lat) if lat else None
    s["lat_p90"] = lat[int(0.9 * (len(lat) - 1))] if lat else None
    costs = []
    for c in ok:
        if c.cost is not None:
            costs.append(c.cost)
        elif price:  # provider didn't report cost — estimate from the catalog price
            costs.append(c.prompt_tokens * price[0] + c.completion_tokens * price[1])
    s["usd_per_turn"] = statistics.mean(costs) if costs else None
    s["prompt_tok"] = statistics.mean(c.prompt_tokens for c in ok) if ok else 0
    s["out_tok"] = statistics.mean(c.completion_tokens for c in ok) if ok else 0
    s["reason_tok"] = statistics.mean(c.reasoning_tokens for c in ok) if ok else 0

    s["providers"] = collections.Counter(c.provider for c in ok)
    s["samples"] = [
        f"{c.scenario.label}: [{a.params.get('proposal_type')}] "
        + re.sub(r"\s+", " ", str(a.params.get("proposal_text", "")))[:150]
        for c in ok if c.scenario.kind == "live"
        for a in c.actions if a.action_type == "create_proposal"][:3]

    fails = []
    if len(ok) < 0.9 * len(calls):
        fails.append("unreliable")
    if s["parse_fail"] > 0.1 * max(len(ok), 1):
        fails.append("unparseable")
    if (s["invented"] or 0) > 10:
        fails.append("invents ids")
    if s["bad"] is not None and s["bad"] >= 25:
        fails.append("backs bad proposals")
    if s["good"] is not None and s["good"] < 40:
        fails.append("blocks good work")
    if s["active_turns"] is not None and s["active_turns"] < 75:
        fails.append("idles")
    s["verdict"] = ", ".join(fails) or "PASS"
    return s


def _f(v, fmt="{:.0f}", none="—"):
    return none if v is None else fmt.format(v)


def report(results: dict[str, dict]) -> None:
    print("\n" + "=" * 118)
    print(f"{'model':42} {'ok':>6} {'active':>6} {'ids✗':>5} {'good':>5} {'bad':>5} "
          f"{'obj':>4} {'idle':>5} {'p50 s':>6} {'p90 s':>6} {'$/1k turns':>10}  verdict")
    print("-" * 118)
    for m, s in results.items():
        per_k = s["usd_per_turn"] * 1000 if s["usd_per_turn"] is not None else None
        print(f"{m[:42]:42} {s['ok']:>3}/{s['calls']:<2} {_f(s['active_turns']):>5}% "
              f"{_f(s['invented']):>4}% {_f(s['good']):>4}% {_f(s['bad']):>4}% {s['objections']:>4} "
              f"{_f(s['wasted']):>4}% {_f(s['lat_med'], '{:.1f}'):>6} {_f(s['lat_p90'], '{:.1f}'):>6} "
              f"{_f(per_k, '{:.2f}'):>10}  {s['verdict']}")
    print("-" * 118)
    print("active = turns that did something  ids✗ = actions carrying an id that matches nothing in state")
    print("good/bad = support rate on clearly good / clearly bad proposals (bad must stay < 25% to fail)")
    print("obj = objection comments on the bad ones  idle = empty-board turns spent doing nothing")
    for m, s in results.items():
        print(f"\n── {m}")
        print(f"   tokens/turn: {s['prompt_tok']:.0f} in, {s['out_tok']:.0f} out "
              f"({s['reason_tok']:.0f} reasoning)   parse failures: {s['parse_fail']}   "
              f"reasoning sent: {s.get('reasoning')}{'  ← NEEDS OVERRIDE' if s.get('needs_override') else ''}")
        if s["errors"]:
            print(f"   errors: {dict(s['errors'])}")
        if s["illegal"]:
            print(f"   illegal action names: {dict(s['illegal'])}")
        if s["invented_examples"]:
            print(f"   invented ids, e.g.: {s['invented_examples']}")
        print(f"   action mix: {dict(s['mix'])}")
        print(f"   served by: {dict(s['providers'].most_common(6))}")
        for line in s["samples"]:
            print(f"   ✎ {line}")


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(REPO, "config.ini"))
        key = cfg.get("openrouter", "api_key", fallback="")
    if not key or key == "YOUR_OPENROUTER_API_KEY_HERE":
        sys.exit("No OpenRouter key: set OPENROUTER_API_KEY or config.ini [openrouter] api_key")
    return key


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--runs", type=int, default=1, help="multiply every scenario's repeats")
    ap.add_argument("--site", default=SITE_API, help="public API to read live communities from")
    ap.add_argument("--live-members", type=int, default=2, help="members sampled per live community")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--json", help="write per-model scores here")
    ap.add_argument("--provider-sort", choices=("price", "throughput", "latency"),
                    help="ask OpenRouter to route by this instead of its default load balancing")
    args = ap.parse_args()
    logging.disable(logging.WARNING)  # the parser logs every bad reply

    async with httpx.AsyncClient(timeout=30) as http:
        catalog = {m["id"]: m for m in (await http.get(f"{OPENROUTER}/models")).json()["data"]}
    models = []
    for m in args.models:
        if m in catalog:
            models.append(m)
        else:
            print(f"  skipping {m}: not in the live OpenRouter catalog (retired or misspelled)")
    price = {m: (float(catalog[m]["pricing"]["prompt"]), float(catalog[m]["pricing"]["completion"]))
             for m in models}

    print("rendering scenarios…")
    scenarios = (await live_scenarios(args.site, args.live_members)
                 + busy_scenarios() + judge_scenarios() + empty_scenarios())
    jobs = [(m, sc) for m in models for sc in scenarios for _ in range(sc.repeat * args.runs)]
    per_model = len(jobs) // max(len(models), 1)
    print(f"{len(scenarios)} scenarios, {per_model} calls per model, {len(models)} models")

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(
        base_url=OPENROUTER,
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(120.0, connect=10.0),
    ) as client:
        settings = dict(zip(models, await asyncio.gather(*(probe_reasoning(client, m) for m in models))))
        prod = {m: openrouter_payload(m, "")["reasoning"] for m in models}
        for m in models:
            if settings[m] != prod[m]:
                print(f"  {m} refuses reasoning={prod[m]} (HTTP 400) — testing it with {settings[m]}; "
                      f"it needs an OPENROUTER_REASONING_OVERRIDES entry to work in the sim")
        t0 = time.monotonic()
        extra = {"provider": {"sort": args.provider_sort}} if args.provider_sort else {}
        calls = await asyncio.gather(*(call_model(client, sem, m, sc, settings[m], extra) for m, sc in jobs))
        print(f"done in {time.monotonic() - t0:.0f}s")

    by_model: dict[str, list[Call]] = collections.defaultdict(list)
    for c in calls:
        by_model[c.model].append(c)
    results = {m: score(by_model[m], price[m]) for m in models}
    for m in models:
        results[m]["reasoning"] = settings[m]
        results[m]["needs_override"] = settings[m] != prod[m]
    report(results)
    spent = sum(c.cost or 0 for c in calls)
    print(f"\nOpenRouter-reported spend for this run: ${spent:.4f}")

    if args.json:
        _write_json(args.json, results)
        print(f"scores written to {args.json}")


def _write_json(path: str, results: dict) -> None:
    with open(path, "w") as f:
        json.dump(results, f, indent=2,
                  default=lambda o: dict(o) if isinstance(o, collections.Counter) else str(o))


if __name__ == "__main__":
    asyncio.run(main())
