"""
Tests for TypeSafe-decided support votes (agents/support_judge.py) and how
the agent turn uses them. No network: the TypeSafe client is faked, so these
pin the request shape, the voting policy and the pulse arithmetic.
"""
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agents.agent import ActionLog, Agent
from agents.community_state import CommunitySnapshot
from agents.decision_engine import AgentAction, build_decision_prompt
from agents.persona import Persona, Traits
from agents.support_judge import (
    MAX_PROPOSALS_PER_TURN,
    ProposalJudgment,
    SupportJudge,
    SupportJudgeUnavailable,
    SupportVotes,
    build_request,
    decide_proposal,
    decide_pulse,
    make_support_judge,
    stance_bar,
)


def _persona(cooperation=0.5, patience=0.5, name="Mei"):
    return Persona(
        name=name, role="Member", traits=Traits(cooperation=cooperation, patience=patience),
        background="Wants concrete conventions.", decision_style="Backs specific rules.",
        communication_style="Brief.",
    )


def _proposal(text="Agents must cite sources.", ptype="AddStatement", status="OutThere",
              support=1, age=0, author=None, **extra):
    p = {
        "id": str(uuid.uuid4()), "proposal_type": ptype, "proposal_status": status,
        "proposal_text": text, "support_count": support, "age": age,
        "user_id": author or str(uuid.uuid4()), "created_at": "2026-09-17T10:00:00Z",
        "val_uuid": None, "val_text": None,
    }
    p.update(extra)
    return p


def _snapshot(out_there=(), on_air=(), members=10, max_age=2, chat=(), comments=None):
    return CommunitySnapshot(
        community={"name": "Commons", "member_count": members},
        variables={"ProposalSupport": "20", "AddStatement": "50", "MaxAge": str(max_age)},
        pulses=[{"id": "next-pulse", "status": 0, "support_count": 0, "threshold": 5}],
        proposals_out_there=list(out_there),
        proposals_on_the_air=list(on_air),
        chat_messages=list(chat),
        proposal_comments=comments or {},
    )


class TestBuildRequest:
    def test_ids_never_reach_the_model(self):
        """Question ids and state keys are short handles; UUIDs stay in code."""
        p = _proposal()
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot([p]),
                            supported_ids=set(), users_cache={}, my_user_id="me")
        assert p["id"] not in json.dumps(req.state)
        assert req.candidates == {"p1": p}
        assert {"stance:p1", "harmful:p1", "concrete:p1"} <= set(req.questions)

    def test_backed_and_own_proposals_are_context_not_candidates(self):
        """Already-backed and self-authored proposals are not re-judged, but
        stay in state so a pending copy of them reads as a duplicate."""
        backed, mine, pending = _proposal("A"), _proposal("B", author="me"), _proposal("C")
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot([backed, mine, pending]),
                            supported_ids={backed["id"]}, users_cache={}, my_user_id="me")
        assert list(req.candidates.values()) == [pending]
        assert len(req.state["proposals"]) == 3
        (key,) = req.candidates
        assert {k for k in req.questions if k.startswith(f"duplicate:{key}:")} == {
            f"duplicate:{key}:{other}" for other in req.backed_keys
        }
        assert len(req.backed_keys) == 2

    def test_later_duplicates_defer_to_earlier_ranked_proposals(self):
        """The better-supported copy is judged first; only the later one is
        asked whether it repeats it."""
        strong, weak = _proposal("X", support=3), _proposal("X again", support=1)
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot([weak, strong]),
                            supported_ids=set(), users_cache={}, my_user_id="me")
        first, second = req.candidates
        assert req.candidates[first] is strong
        assert not any(k.startswith(f"duplicate:{first}:") for k in req.questions)
        assert f"duplicate:{second}:{first}" in req.questions

    def test_target_named_changes_are_compared_in_code(self):
        """Templated Membership texts read alike to a model; the applicant id
        decides whether two applications are the same change."""
        alex = _proposal("Alex wants to join the community", ptype="Membership", val_uuid="u-alex")
        sam = _proposal("Sam wants to join the community", ptype="Membership", val_uuid="u-sam")
        again = _proposal("Alex wants to join the community", ptype="Membership", val_uuid="u-alex")
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot([alex, sam, again]),
                            supported_ids=set(), users_cache={}, my_user_id="me")
        assert not any(k.startswith("duplicate:") for k in req.questions)
        keys = {id(p): k for k, p in req.candidates.items()}
        assert req.same_target == {keys[id(again)]: [keys[id(alex)]]}

    def test_only_same_type_proposals_are_compared(self):
        statement = _proposal("Cite sources", ptype="AddStatement")
        action = _proposal("Citation working group", ptype="AddAction")
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot([statement, action]),
                            supported_ids=set(), users_cache={}, my_user_id="me")
        assert not any(k.startswith("duplicate:") for k in req.questions)

    def test_type_specific_questions(self):
        edit = _proposal("New body", ptype="EditArtifact", _old_content="Old body")
        title = _proposal("Conflict Resolution Process", ptype="CreateArtifact",
                          val_text="Conflict Resolution Process")
        member = _proposal("Welcome me", ptype="Membership", val_uuid="u-1")
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot([edit, title, member]),
                            supported_ids=set(), users_cache={"u-1": "Robin"}, my_user_id="me")
        keys = {p["proposal_type"]: k for k, p in req.candidates.items()}
        e, c, m = keys["EditArtifact"], keys["CreateArtifact"], keys["Membership"]
        assert req.state["proposals"][e]["current_text"] == "Old body"
        assert f"improves:{e}" in req.questions
        assert "section_title" in req.questions[f"concrete:{c}"]["instructions"]
        assert f"concrete:{m}" not in req.questions, "a one-line Membership text is normal, not vague"
        assert req.state["proposals"][m]["applicant"] == "Robin"

    def test_applicants_are_named_from_their_own_proposal(self):
        """Applicants are not members yet, so they are not in users_cache."""
        p = _proposal("I want in", ptype="Membership", val_uuid="u-9", author="u-9", user_name="sage_applicant")
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot([p]),
                            supported_ids=set(), users_cache={}, my_user_id="me")
        assert req.state["proposals"]["p1"]["applicant"] == "sage_applicant"

    def test_only_other_members_comments_can_object(self):
        p = _proposal()
        comments = {p["id"]: [
            {"user_id": "me", "comment_text": "my own note"},
            {"user_id": "u-2", "comment_text": "Conflicts with rule 2."},
        ]}
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot([p], comments=comments),
                            supported_ids=set(), users_cache={"u-2": "Yael"}, my_user_id="me")
        assert req.state["proposals"]["p1"]["comments"] == ["Yael: Conflicts with rule 2."]
        assert "objection:p1" in req.questions

    def test_chat_is_read_only_when_a_pulse_could_decide_something(self):
        chat = [{"user_id": "u-2", "comment_text": "Hold the pulse please"}]
        with_board = build_request(persona=_persona(), intention="", snapshot=_snapshot([_proposal()], chat=chat),
                                   supported_ids=set(), users_cache={}, my_user_id="me")
        empty = build_request(persona=_persona(), intention="", snapshot=_snapshot(chat=chat),
                              supported_ids=set(), users_cache={}, my_user_id="me")
        assert {"chat:wait", "chat:pulse"} <= set(with_board.questions)
        assert empty.questions == {}

    def test_current_plan_is_used_only_when_there_is_one(self):
        snap = _snapshot([_proposal()])
        planned = build_request(persona=_persona(), intention="Get attribution adopted", snapshot=snap,
                                supported_ids=set(), users_cache={}, my_user_id="me")
        unplanned = build_request(persona=_persona(), intention="", snapshot=snap,
                                  supported_ids=set(), users_cache={}, my_user_id="me")
        assert planned.state["member"]["current_plan"] == "Get attribution adopted"
        assert "current plan" in planned.questions["stance:p1"]["instructions"]
        assert "current_plan" not in unplanned.state["member"]
        assert "current plan" not in unplanned.questions["stance:p1"]["instructions"]

    def test_judges_a_bounded_number_per_turn(self):
        props = [_proposal(f"P{i}") for i in range(MAX_PROPOSALS_PER_TURN + 3)]
        req = build_request(persona=_persona(), intention="", snapshot=_snapshot(props),
                            supported_ids=set(), users_cache={}, my_user_id="me")
        assert len(req.candidates) == MAX_PROPOSALS_PER_TURN


class TestDecideProposal:
    def _j(self, **kw):
        base = dict(stance=2.0, harmful=0.1, concrete=0.9, improves=None, duplicate=None, objection=None)
        base.update(kw)
        return ProposalJudgment(**base)

    def test_good_proposal_is_backed(self):
        assert decide_proposal(_persona(), "AddStatement", self._j()) == (True, "Helps something I care about")

    def test_vagueness_sinks_text_types_only(self):
        assert decide_proposal(_persona(), "AddStatement", self._j(concrete=0.1))[0] is False
        assert decide_proposal(_persona(), "Membership", self._j(concrete=0.1))[0] is True

    @pytest.mark.parametrize("field,value,reason", [
        ("improves", 0.1, "Worse than the text it replaces"),
        ("duplicate", 0.9, "Repeats a proposal I already back"),
        ("harmful", 0.9, "Would damage the community"),
    ])
    def test_disqualifiers(self, field, value, reason):
        assert decide_proposal(_persona(), "EditArtifact", self._j(**{field: value})) == (False, reason)

    def test_harm_needs_a_confident_yes(self):
        """Bad-but-ordinary proposals score up to ~0.6 on harm; they must be
        caught by the other checks, not by calling them damage."""
        assert decide_proposal(_persona(), "AddStatement", self._j(harmful=0.6))[0] is True

    def test_cooperation_moves_the_bar(self):
        lukewarm = self._j(stance=1.3)
        assert stance_bar(_persona(cooperation=0.9)) < 1.3 < stance_bar(_persona(cooperation=0.3))
        assert decide_proposal(_persona(cooperation=0.9), "AddStatement", lukewarm)[0] is True
        assert decide_proposal(_persona(cooperation=0.3), "AddStatement", lukewarm)[0] is False

    def test_a_real_objection_raises_the_bar(self):
        assert decide_proposal(_persona(), "AddStatement", self._j(stance=1.8))[0] is True
        assert decide_proposal(_persona(), "AddStatement", self._j(stance=1.8, objection=0.9)) == (
            False, "Not convinced: a comment points out a real flaw")

    def test_opposition_is_named(self):
        assert decide_proposal(_persona(), "AddStatement", self._j(stance=0.2)) == (False, "Works against what I want")


class TestDecidePulse:
    """decide_pulse mirrors PulseService: OnTheAir accepted at
    support >= threshold, OutThere at MaxAge canceled before promotion."""

    def _vote(self, snap, backs=(), newly=(), persona=None, **kw):
        return decide_pulse(persona or _persona(), snap, backs=set(backs), newly_backed=set(newly),
                            already_supported=kw.pop("already", False), **kw)

    def test_empty_board_never_pulses(self):
        assert self._vote(_snapshot()) == (False, "No open proposals: a pulse would decide nothing")

    def test_already_supported(self):
        assert self._vote(_snapshot([_proposal()]), already=True)[0] is False

    def test_my_new_support_can_be_the_deciding_vote(self):
        """Support cast this turn is not on the server yet: 4 + 1 reaches the
        threshold of 5, while 4 on its own would sink the proposal."""
        mine = _proposal(status="OnTheAir", support=4)
        snap = _snapshot(on_air=[mine])
        pulse, why = self._vote(snap, backs=[mine["id"]], newly=[mine["id"]])
        assert pulse is True and "pass 1 proposal I back" in why
        pulse, why = self._vote(snap, backs=[mine["id"]])
        assert pulse is False and "sink 1 proposal I back" in why

    def test_holds_when_it_would_sink_what_i_back(self):
        mine = _proposal(status="OnTheAir", support=1)
        pulse, why = self._vote(_snapshot(on_air=[mine]), backs=[mine["id"]])
        assert pulse is False and "sink 1 proposal I back that still lacks support" in why

    def test_holds_when_it_would_pass_what_i_declined(self):
        theirs = _proposal(status="OnTheAir", support=9)
        pulse, why = self._vote(_snapshot(on_air=[theirs]))
        assert pulse is False and "pass 1 I don't back" in why

    def test_aging_out_cancels_even_with_enough_support(self):
        """PulseService cancels at MaxAge before checking promotion."""
        aging = _proposal(support=9, age=2)
        pulse, why = self._vote(_snapshot([aging], max_age=2), backs=[aging["id"]])
        assert pulse is False and "cancel 1 I back that is aging out" in why
        stale = _proposal(support=0, age=2)
        pulse, why = self._vote(_snapshot([stale, _proposal(), _proposal()], max_age=2))
        assert pulse is True and "retire 1 stale one I don't back" in why

    def test_thin_board_needs_a_clear_win(self):
        """#217: with one or two proposals open, pulsing resolves them before
        others have weighed in, so break-even is not enough."""
        win, loss = _proposal(status="OnTheAir", support=9), _proposal(status="OnTheAir", support=9)
        snap = _snapshot(on_air=[win, loss])
        assert self._vote(snap, backs=[win["id"]])[0] is False  # +1 -1 on a thin board
        busy = _snapshot(on_air=[win, loss], out_there=[_proposal()])
        assert self._vote(busy, backs=[win["id"]])[0] is True   # same trade on a busy board

    def test_patience_and_chat_shift_the_bar(self):
        p = _proposal(status="OnTheAir", support=9)
        snap = _snapshot(on_air=[p])
        assert self._vote(snap, backs=[p["id"]])[0] is True
        assert self._vote(snap, backs=[p["id"]], persona=_persona(patience=0.9))[0] is False
        assert self._vote(snap, backs=[p["id"]], chat_wait=True) == (False, "Holding the pulse: chat asked to wait")
        assert self._vote(snap, backs=[p["id"]], persona=_persona(patience=0.9), chat_pulse=True)[0] is True


def _fake_response(state, questions, overrides=None):
    """Answer every question with a neutral-good default; `overrides` maps a
    proposal text (or "chat") to {judgment: value}, where "duplicate_of" names
    the text of the one proposal it repeats."""
    overrides = overrides or {}
    texts = {k: v.get("text") or v.get("proposed_text") for k, v in state.get("proposals", {}).items()}
    by_key = {k: overrides.get(t, {}) for k, t in texts.items()}
    defaults = {"stance": 2.0, "harmful": 0.1, "concrete": 0.9, "improves": 0.9,
                "duplicate": 0.1, "objection": 0.1, "chat": 0.1}
    scores, nouls = {}, {}
    for qid, q in questions.items():
        kind, key, *other = qid.split(":")
        if kind == "duplicate":
            value = 0.9 if by_key[key].get("duplicate_of") == texts[other[0]] else None
        elif kind == "chat":
            value = overrides.get("chat", {}).get(key)
        else:
            value = by_key[key].get(kind)
        value = defaults[kind] if value is None else value
        if q["type"] == "score":
            scores[qid] = SimpleNamespace(score=value)
        else:
            nouls[qid] = SimpleNamespace(noul=value)
    return SimpleNamespace(scores=scores, nouls=nouls, model="jev-test")


class _FakeClient:
    def __init__(self, overrides=None, fail=False):
        self.calls = []
        self.overrides = overrides
        self.fail = fail

    async def system_one(self, state, questions, model=None):
        self.calls.append((state, questions, model))
        if self.fail:
            raise RuntimeError("529 overloaded")
        return _fake_response(state, questions, self.overrides)

    async def aclose(self):
        pass


async def _judge_vote(judge, snap, **kw):
    return await judge.vote(persona=kw.get("persona", _persona()), intention="", snapshot=snap,
                            supported_proposals=kw.get("supported", set()), supported_pulse_ids=set(),
                            users_cache={}, my_user_id="me")


class TestSupportJudge:
    async def test_votes_become_actions(self):
        good = _proposal("Cite the artifact id.", status="OnTheAir", support=4)
        vague = _proposal("Be respectful.")
        client = _FakeClient({"Be respectful.": {"concrete": 0.05}})
        votes = await _judge_vote(SupportJudge(client), _snapshot([vague, _proposal("Third")], on_air=[good]))

        assert len(client.calls) == 1, "one request per turn"
        backed = {a.params["proposal_id"] for a in votes.actions() if a.action_type == "support_proposal"}
        assert good["id"] in backed and vague["id"] not in backed
        assert votes.pulse is True
        assert votes.actions()[-1].action_type == "support_pulse"
        lines = votes.prompt_lines()
        assert any(line.startswith("✗ declining") and "Too vague to commit to" in line for line in lines)
        assert lines[-1].startswith("pulse: supporting")

    async def test_only_a_copy_of_something_backed_is_a_duplicate(self):
        """A concrete rewrite of a vague idea the member turned down is the
        better version, not a duplicate; a copy of a proposal the member
        backs this very turn is."""
        vague = _proposal("Be careful with attribution.", support=5)
        concrete = _proposal("Cite the source artifact id in line one.", support=2)
        backed = _proposal("Rate-limit requests to 10 per minute.", support=4)
        copy = _proposal("Cap requests at ten per minute.", support=1)
        client = _FakeClient({
            "Be careful with attribution.": {"concrete": 0.05},
            "Cite the source artifact id in line one.": {"duplicate_of": "Be careful with attribution."},
            "Cap requests at ten per minute.": {"duplicate_of": "Rate-limit requests to 10 per minute."},
        })
        votes = await _judge_vote(SupportJudge(client), _snapshot([vague, concrete, backed, copy]))
        by_id = {v.proposal["id"]: v for v in votes.proposals}
        assert by_id[vague["id"]].support is False
        assert by_id[concrete["id"]].support is True
        assert by_id[backed["id"]].support is True
        assert (by_id[copy["id"]].support, by_id[copy["id"]].reason) == (False, "Repeats a proposal I already back")

    async def test_nothing_to_judge_makes_no_request(self):
        mine = _proposal(status="OnTheAir", support=9, author="me")
        client = _FakeClient()
        votes = await _judge_vote(SupportJudge(client), _snapshot(on_air=[mine]))
        assert client.calls == []
        assert votes.proposals == [] and votes.pulse is True  # still votes the pulse, from code alone

    async def test_client_errors_propagate_and_are_counted(self):
        """The agent catches this and lets the LLM vote for the turn."""
        judge = SupportJudge(_FakeClient(fail=True))
        with pytest.raises(RuntimeError):
            await _judge_vote(judge, _snapshot([_proposal()]))
        assert judge.stats["errors"] == 1


class TestMakeSupportJudge:
    @pytest.fixture(autouse=True)
    def _no_key(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)  # no config.ini here

    def test_llm_mode(self):
        assert make_support_judge("llm") is None

    def test_auto_falls_back_to_the_llm_without_a_key(self):
        assert make_support_judge("auto") is None

    def test_typesafe_mode_fails_loudly_without_a_key(self):
        with pytest.raises(SupportJudgeUnavailable, match="TYPESAFE_API_KEY"):
            make_support_judge("typesafe")

    def test_unknown_mode(self):
        with pytest.raises(ValueError):
            make_support_judge("gpt")

    async def test_key_from_config_ini(self, tmp_path):
        pytest.importorskip("typesafe_sdk")
        (tmp_path / "config.ini").write_text("[typesafe]\napi_key = apikey_test\n")
        judge = make_support_judge("auto")
        assert isinstance(judge, SupportJudge)
        await judge.aclose()


class TestPromptWithVotes:
    def _prompt(self, votes):
        return build_decision_prompt(
            persona_name="Y", persona_role="m", persona_background="b",
            persona_decision_style="d", persona_communication_style="c",
            persona_trait_summary="t", community_summary="S", action_history=[],
            unsupported_proposals=["[AddStatement] \"x\" id=11111111"], total_active_proposals=4,
            support_votes=votes,
        )

    def test_votes_replace_the_judgment_queue(self):
        p = self._prompt(["✓ backing [AddStatement] \"x\" id=P-11111111 — Helps something I care about",
                          "pulse: not supporting — Holding the pulse: chat asked to wait"])
        assert "Your Votes This Turn (already decided)" in p
        assert "✓ backing [AddStatement]" in p
        assert "VOTES — ALREADY CAST" in p
        assert "Do NOT emit either action" in p
        assert "Proposals Awaiting YOUR Judgment" not in p
        assert "PULSE STRATEGY —" not in p
        assert "do_nothing** — FAILURE" not in p, "votes already count as taking part"

    def test_without_votes_the_prompt_is_unchanged(self):
        p = self._prompt(None)
        assert "Proposals Awaiting YOUR Judgment" in p
        assert "PULSE STRATEGY — DEFAULT IS TO SUPPORT!" in p
        assert "do_nothing** — FAILURE" in p
        assert "Your Votes This Turn" not in p


class TestAgentTurnWithJudge:
    def _agent(self, judge, llm_decisions):
        engine = SimpleNamespace(calls=[])

        async def decide(**kw):
            engine.calls.append(kw)
            return list(llm_decisions)
        engine.decide = decide
        agent = Agent(persona=_persona(), client=None, engine=engine, user_id="me", support_judge=judge)
        agent.community_id = "c1"
        executed = []

        async def execute(decision, snapshot):
            executed.append(decision)
            return ActionLog(datetime.now(timezone.utc), decision.action_type, decision.reason, "ok", True)
        agent._execute_action = execute
        return agent, engine, executed

    def _board(self):
        good = _proposal("Cite the artifact id.", status="OnTheAir", support=4)
        return good, _snapshot([_proposal("Second"), _proposal("Third")], on_air=[good])

    async def _turn(self, agent, snap):
        async def observe():
            return snap
        agent.observe = observe
        return await agent.think_and_act()

    async def test_judge_votes_and_llm_writes(self):
        good, snap = self._board()
        llm = [
            AgentAction("support_proposal", "LLM vote", {"proposal_id": "P-deadbeef"}),
            AgentAction("support_pulse", "LLM pulse"),
            AgentAction("comment", "object", {"proposal_id": good["id"], "comment_text": "hm"}),
            AgentAction("do_nothing", "nothing", {"update_intention": "Get attribution adopted"}),
        ]
        agent, engine, executed = self._agent(SupportJudge(_FakeClient()), llm)
        await self._turn(agent, snap)

        assert engine.calls[0]["support_votes"], "the LLM is shown the votes"
        reasons = [d.reason for d in executed]
        assert "LLM vote" not in reasons and "LLM pulse" not in reasons
        assert "nothing" not in reasons, "votes are the turn; do_nothing is dropped"
        assert executed[-1].action_type == "support_pulse", "pulse still runs last"
        assert any(d.action_type == "comment" for d in executed)
        assert agent.current_intention == "Get attribution adopted", "intention survives the dropped do_nothing"

    async def test_filing_a_proposal_holds_the_pulse(self):
        _, snap = self._board()
        llm = [AgentAction("create_proposal", "new", {"proposal_type": "AddStatement", "proposal_text": "x"})]
        agent, _, executed = self._agent(SupportJudge(_FakeClient()), llm)
        await self._turn(agent, snap)
        assert "support_pulse" not in [d.action_type for d in executed]
        assert "create_proposal" in [d.action_type for d in executed]

    async def test_judge_failure_hands_the_votes_back_to_the_llm(self):
        _, snap = self._board()
        llm = [AgentAction("support_pulse", "LLM pulse")]
        agent, engine, executed = self._agent(SupportJudge(_FakeClient(fail=True)), llm)
        await self._turn(agent, snap)
        assert engine.calls[0]["support_votes"] is None
        assert [d.reason for d in executed] == ["LLM pulse"]

    def test_merge_keeps_do_nothing_when_no_votes_were_cast(self):
        agent = Agent(persona=_persona(), client=None, engine=None, user_id="me")
        merged = agent._merge_support_votes([AgentAction("do_nothing", "idle")], SupportVotes())
        assert [d.action_type for d in merged] == ["do_nothing"]
