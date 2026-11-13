#!/usr/bin/env python3
"""
Benchmark agents' support votes: the TypeSafe judge vs. the LLM prompt.

Builds one fixture board — a Commons-style community holding proposals whose
quality is known: clearly GOOD, clearly BAD, and AGENDA-dependent (no right
answer; they should split members along their priorities) — and asks every
YAML persona how it votes. Reports support rates on GOOD vs BAD, the
per-persona votes, and each persona's pulse vote.

Usage:
    python -m agents.bench_support
    python -m agents.bench_support --chat wait          # chat asks to hold the pulse
    python -m agents.bench_support --compare-llm or-mercury-2.5 --turns 2

Needs a TypeSafe key (TYPESAFE_API_KEY or config.ini [typesafe] api_key);
--compare-llm also needs that preset's LLM credentials.
"""
import argparse
import asyncio
import uuid

from agents.agent import Agent
from agents.communities import COMMONS
from agents.community_state import CommunitySnapshot
from agents.decision_engine import DecisionEngine
from agents.persona import load_all_personas
from agents.support_judge import SupportJudge

_ONBOARDING = (
    "## Onboarding a new agent\n"
    "1. The newcomer posts one message naming its owner and what it is here to do.\n"
    "2. An existing member replies within one pulse and links the adopted Conventions.\n"
    "3. The newcomer's first proposal is reviewed by that member, who comments before supporting.\n"
    "4. After two pulses the newcomer may support proposals without review."
)
_PLATITUDE = (
    "Onboarding should be a welcoming and excellent experience. We value quality, "
    "respect and collaboration, and every new agent should feel supported on their journey."
)

# (name, label, status, type, text, extra fields)
_PROPOSALS = [
    ("g_cite", "G", "OnTheAir", "AddStatement",
     "An agent that re-publishes another agent's artifact must cite the artifact id it derived from in the first line of the new artifact.",
     dict(pitch="Attribution is the biggest gap in our Conventions; this makes derived work traceable.", support_count=4)),
    ("g_onboard", "G", "OutThere", "EditArtifact", _ONBOARDING,
     dict(pitch="Fills the empty onboarding section with a concrete four-step procedure.", artifact="onboarding")),
    ("g_member", "G", "OutThere", "Membership",
     "I maintain two open-source agent frameworks and want to help draft the attribution conventions.",
     dict(target_user="Robin")),
    ("g_retire", "G", "OutThere", "RemoveStatement",
     "This rule points at a #general channel the community deleted months ago; nobody can comply with it.",
     dict(statement="stale")),
    ("a_maxage", "A", "OnTheAir", "ChangeVariable",
     "MaxAge\nRaise to 3: four of the last six proposals were canceled before most members had a turn to read them.",
     dict(val_text="3", support_count=3)),
    ("a_pulse30", "A", "OutThere", "ChangeVariable",
     "PulseSupport\nLower to 30 so the community can decide faster.",
     dict(val_text="30", pitch="We are too slow; fewer supporters should be able to trigger a pulse.")),
    ("b_vague", "B", "OnTheAir", "AddStatement",
     "Agents should always be respectful and act with integrity toward each other.",
     dict(pitch="Respect matters.", support_count=5)),
    ("b_platitude", "B", "OutThere", "EditArtifact", _PLATITUDE,
     dict(pitch="Makes onboarding friendlier.", artifact="filled", age=2)),
    ("b_dup_cite", "B", "OutThere", "AddStatement",
     "When an agent republishes work derived from another agent's artifact, it has to reference the source artifact's id at the top.",
     dict(pitch="We need attribution rules.")),
    ("b_throwout", "B", "OutThere", "ThrowOut",
     "Remove Priya. She comments on everything and slows us down.",
     dict(target_user="Priya")),
    ("b_ratelimit", "B", "OutThere", "AddStatement",
     "An agent may send at most 100 requests per minute to another owner's agent.",
     dict(comment=("Yael", "Rule 2 already caps this at 10 requests per minute. Adopting this creates a second, conflicting limit of 100."))),
    ("b_slogan", "B", "OutThere", "CreateArtifact", "Our Vision for the Future",
     dict(val_text="Our Vision for the Future")),
]

_CHAT = {
    "wait": ("Noa", "Please hold the pulse one more round. The onboarding edit only went up this morning and most of us haven't read it."),
    "pulse": ("Omer", "Several proposals are ready to be decided. Let's all support the pulse now."),
    "noise": ("Yael", "Has anyone tried the new MCP inspector? Pretty handy for debugging."),
}


def _id() -> str:
    return str(uuid.uuid4())


def fixture_board(personas, chat: str | None):
    """Returns (snapshot, labels, names, users_cache, persona user ids)."""
    users = {p.name: _id() for p in personas}
    for outsider in ("Noa", "Yael", "Omer", "Robin"):
        users[outsider] = _id()
    users_cache = {uid: name for name, uid in users.items()}

    rules = [
        {"id": _id(), "statement_text": "An agent must state which owner it acts for when it first posts in a community."},
        {"id": _id(), "statement_text": "An agent may not send more than 10 requests per minute to another owner's agent without that owner's consent."},
        {"id": _id(), "statement_text": "Agents must introduce themselves in the #general channel before proposing anything."},
    ]
    container = {"id": _id(), "title": "Conventions", "mission": COMMONS.mission, "status": 1}
    artifacts = {
        "onboarding": {"id": _id(), "title": "Onboarding a new agent", "content": "",
                       "author_user_id": users["Noa"]},
        "filled": {"id": _id(), "title": "Onboarding a new agent (adopted)", "content": _ONBOARDING,
                   "author_user_id": users["Yael"]},
    }
    members = [n for n in users if n != "Robin"]
    snap = CommunitySnapshot(
        community={"name": COMMONS.name, "member_count": len(members)},
        variables={"PulseSupport": "50", "ProposalSupport": "15", "MaxAge": "2"},
        members=[{"user_id": users[n], "seniority": 3} for n in members],
        statements=rules,
        pulses=[{"id": _id(), "status": 0, "support_count": 1, "threshold": 5}],
        containers=[container],
        container_artifacts={container["id"]: list(artifacts.values())},
    )
    labels, names = {}, {}
    authors = ["Noa", "Yael", "Omer"]
    for i, (name, label, status, ptype, text, extra) in enumerate(_PROPOSALS):
        p = {
            "id": _id(), "proposal_type": ptype, "proposal_status": status,
            "proposal_text": text, "pitch": extra.get("pitch"),
            "user_id": users[authors[i % len(authors)]],
            "support_count": extra.get("support_count", 1), "age": extra.get("age", 0),
            "created_at": f"2026-09-17T10:{i:02d}:00Z",
            "val_text": extra.get("val_text"), "val_uuid": None,
        }
        if "artifact" in extra:
            art = artifacts[extra["artifact"]]
            p["val_uuid"] = art["id"]
            p["_old_content"] = art["content"]
            p["_display"] = f'Edit: "{art["title"]}"'
        if "target_user" in extra:
            p["val_uuid"] = users[extra["target_user"]]
        if extra.get("statement") == "stale":
            p["val_uuid"] = rules[2]["id"]
        if "comment" in extra:
            who, text_ = extra["comment"]
            snap.proposal_comments[p["id"]] = [{"user_id": users[who], "comment_text": text_}]
        (snap.proposals_on_the_air if status == "OnTheAir" else snap.proposals_out_there).append(p)
        labels[p["id"]] = label
        names[p["id"]] = name
    if chat:
        who, text_ = _CHAT[chat]
        snap.chat_messages = [{"user_id": users[who], "comment_text": text_}]
    return snap, labels, names, users_cache, users


async def run_typesafe(personas, snap, users_cache, users):
    judge = SupportJudge.from_config()
    try:
        results = await asyncio.gather(*(
            judge.vote(
                persona=p, intention="", snapshot=snap, supported_proposals=set(),
                supported_pulse_ids=set(), users_cache=users_cache, my_user_id=users[p.name],
            )
            for p in personas
        ))
    finally:
        await judge.aclose()
    return {p.name: v for p, v in zip(personas, results)}


async def run_llm(preset: str, personas, snap, users_cache, users, turns: int):
    from agents.simulation_api import LLM_PRESETS
    cfg = LLM_PRESETS[preset]
    engine = DecisionEngine(backend=cfg["backend"], model=cfg["model"], ollama_think=cfg.get("think", False))
    open_props = snap.proposals_out_there + snap.proposals_on_the_air

    async def one(persona):
        resolver = Agent(persona=persona, client=None, engine=engine)
        summary = snap.summarize(my_user_id=users[persona.name], users_cache=users_cache, supported_proposals=set())
        unsupported = [f"[{p['proposal_type']}] \"{p['proposal_text'][:60]}\" id={p['id'][:8]}" for p in open_props]
        runs = []
        for _ in range(turns):
            decisions = await engine.decide(
                persona_name=persona.name, persona_role=persona.role,
                persona_background=persona.background, persona_decision_style=persona.decision_style,
                persona_communication_style=persona.communication_style,
                persona_trait_summary=persona.trait_summary(), community_summary=summary,
                action_history=[], unsupported_proposals=unsupported, already_supported_proposals=[],
                already_commented=[], consecutive_do_nothings=0, initiative=persona.traits.initiative,
                total_active_proposals=len(open_props), interview_context="", memory_context="",
                recent_failures=[],
            )
            resolved = [
                resolver._resolve_proposal_id(d.params.get("proposal_id", ""), snap)
                for d in decisions if d.action_type == "support_proposal"
            ]
            unresolved[0] += resolved.count("")
            runs.append((set(resolved) - {""}, any(d.action_type == "support_pulse" for d in decisions)))
        return runs

    unresolved = [0]
    results = await asyncio.gather(*(one(p) for p in personas))
    if unresolved[0]:
        print(f"[llm] {unresolved[0]} support_proposal vote(s) named an id that matches no proposal (not counted)")
    return {p.name: r for p, r in zip(personas, results)}


def _rate(votes: list[bool]) -> str:
    return f"{100 * sum(votes) / len(votes):5.1f}% ({sum(votes)}/{len(votes)})" if votes else "   n/a"


def report(personas, labels, names, ts, llm, llm_label):
    order = [pid for pid in labels]
    print("\nSupport rate                GOOD                 BAD")
    for label_name, per_persona in [("typesafe", {n: [{v.proposal["id"] for v in votes.proposals if v.support}] for n, votes in ts.items()})] + (
        [(f"llm {llm_label}", {n: [b for b, _ in runs] for n, runs in llm.items()})] if llm else []
    ):
        good = [pid in backed for runs in per_persona.values() for backed in runs for pid in order if labels[pid] == "G"]
        bad = [pid in backed for runs in per_persona.values() for backed in runs for pid in order if labels[pid] == "B"]
        print(f"  {label_name:24}  {_rate(good):18}  {_rate(bad)}")

    header = "".join(f"{p.name[:7]:>8}" for p in personas)
    print(f"\nTypeSafe votes (✓ back · decline){'':6}{header}")
    for pid in order:
        cells = ""
        for p in personas:
            backed = {v.proposal["id"]: v.support for v in ts[p.name].proposals}
            cells += f"{'✓' if backed.get(pid) else '·':>8}"
        print(f"  {names[pid]:12} {labels[pid]:3}{'':19}{cells}")
    print(f"  {'pulse':16}{'':19}" + "".join(f"{'✓' if ts[p.name].pulse else '·':>8}" for p in personas))
    if llm:
        print(f"\nLLM {llm_label} back rate per proposal{'':3}{header}")
        for pid in order:
            cells = "".join(
                f"{sum(pid in b for b, _ in llm[p.name]) / len(llm[p.name]):8.2f}" for p in personas
            )
            print(f"  {names[pid]:12} {labels[pid]:3}{'':19}{cells}")
        print(f"  {'pulse':16}{'':19}" + "".join(
            f"{sum(pl for _, pl in llm[p.name]) / len(llm[p.name]):8.2f}" for p in personas))

    print("\nTypeSafe reasons")
    for p in personas:
        v = ts[p.name]
        chat = "" if v.chat_wait is None else f"  [chat wait={v.chat_wait:.2f} pulse={v.chat_pulse:.2f}]"
        print(f"  {p.name}: pulse {'✓' if v.pulse else '·'} — {v.pulse_reason}{chat}")
        for pv in v.proposals:
            j = pv.judgment
            raw = f"stance={j.stance:.2f} harm={j.harmful:.2f}" + "".join(
                f" {k}={getattr(j, k):.2f}" for k in ("concrete", "improves", "duplicate", "objection")
                if getattr(j, k) is not None
            )
            print(f"      {'✓' if pv.support else '·'} {names[pv.proposal['id']]:12} {pv.reason:48} {raw}")


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chat", choices=sorted(_CHAT), help="add one chat message to the board")
    parser.add_argument("--compare-llm", metavar="PRESET", help="also run the LLM prompt (an LLM_PRESETS key)")
    parser.add_argument("--turns", type=int, default=1, help="LLM samples per persona (default 1)")
    args = parser.parse_args()

    personas = load_all_personas()
    snap, labels, names, users_cache, users = fixture_board(personas, args.chat)
    ts = await run_typesafe(personas, snap, users_cache, users)
    llm = await run_llm(args.compare_llm, personas, snap, users_cache, users, args.turns) if args.compare_llm else None
    report(personas, labels, names, ts, llm, args.compare_llm)


if __name__ == "__main__":
    asyncio.run(main())
