"""
Tests for the agent system.

Tests persona loading, community observation, decision parsing,
and the full agent observe → think → act loop with a mock LLM.
"""
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agents.api_client import KBZClient
from agents.agent import Agent
from agents.community_state import CommunitySnapshot
from agents.decision_engine import AgentAction, DecisionEngine, build_decision_prompt
from agents.persona import Persona, Traits, load_all_personas, load_persona



# --- Persona Tests ---


class TestPersona:
    def test_load_all_personas(self):
        personas = load_all_personas()
        assert len(personas) == 6
        names = [p.name for p in personas]
        assert "Mei" in names
        assert "Henrik" in names
        assert "Priya" in names
        assert "Diego" in names
        assert "Sofia" in names
        assert "Marcus" in names

    def test_load_single_persona(self):
        persona_dir = Path(__file__).parent.parent / "agents" / "personas"
        persona = load_persona(str(persona_dir / "progressive.yaml"))
        assert persona.name == "Mei"
        assert persona.role == "Community Visionary"
        assert persona.traits.openness == 0.9
        assert persona.traits.patience == 0.3

    def test_trait_summary(self):
        persona = Persona(
            name="Test",
            role="Tester",
            traits=Traits(openness=0.9, cooperation=0.3, initiative=0.8, patience=0.2, social_energy=0.9, confrontation=0.1),
            background="",
            decision_style="",
            communication_style="",
        )
        summary = persona.trait_summary()
        assert "very open to new ideas" in summary
        assert "independent-minded" in summary
        assert "proactive" in summary
        assert "impatient" in summary
        assert "socially active" in summary
        assert "avoids confrontation" in summary


# --- API client error mapping ---


class TestKBZAPIError:
    """The agent-facing API client maps 4xx/5xx into KBZAPIError
    carrying the FastAPI `detail`. Without this, the agent's
    failure log contains only the bare httpx wrapper text and the
    LLM has nothing to learn from."""

    def test_dict_detail_is_extracted(self):
        from agents.api_client import _check, KBZAPIError
        import httpx
        request = httpx.Request("POST", "http://x/communities/c/proposals")
        resp = httpx.Response(
            422, request=request,
            json={"detail": "AddStatement requires non-empty proposal_text"},
        )
        with pytest.raises(KBZAPIError) as exc:
            _check(resp)
        assert exc.value.status_code == 422
        assert "non-empty" in exc.value.detail

    def test_list_detail_is_collapsed(self):
        """FastAPI 422 from pydantic is a list of {loc, msg, ...}.
        Collapsed to a semicolon-joined string so the failure block
        reads as one line per failed call."""
        from agents.api_client import _check, KBZAPIError
        import httpx
        request = httpx.Request("POST", "http://x/")
        resp = httpx.Response(
            422, request=request,
            json={"detail": [
                {"loc": ["body", "user_id"], "msg": "field required"},
                {"loc": ["body", "amount"], "msg": "value is not a valid Decimal"},
            ]},
        )
        with pytest.raises(KBZAPIError) as exc:
            _check(resp)
        assert "field required" in exc.value.detail
        assert "valid Decimal" in exc.value.detail

    def test_success_passes_through(self):
        from agents.api_client import _check
        import httpx
        request = httpx.Request("GET", "http://x/")
        resp = httpx.Response(200, request=request, json={"ok": True})
        _check(resp)  # must not raise


# --- Decision Engine Tests ---


class TestDecisionEngine:
    def test_parse_response_json(self):
        engine = DecisionEngine()
        text = '{"action": "support_pulse", "reason": "Time to advance"}'
        result = engine._parse_response(text)
        assert result[0].action_type == "support_pulse"
        assert result[0].reason == "Time to advance"

    def test_parse_response_array(self):
        engine = DecisionEngine()
        text = json.dumps([
            {"action": "create_proposal", "proposal_type": "AddStatement",
             "proposal_text": "test", "reason": "reason1"},
            {"action": "support_proposal", "proposal_id": "P-abc123def", "reason": "reason2"},
        ])
        result = engine._parse_response(text)
        assert len(result) == 2
        assert result[0].action_type == "create_proposal"
        assert result[1].action_type == "support_proposal"

    def test_parse_response_with_code_fence(self):
        engine = DecisionEngine()
        text = '```json\n{"action": "do_nothing", "reason": "Observing"}\n```'
        result = engine._parse_response(text)
        assert result[0].action_type == "do_nothing"

    def test_parse_response_with_params(self):
        engine = DecisionEngine()
        text = json.dumps({
            "action": "create_proposal",
            "proposal_type": "AddStatement",
            "proposal_text": "All members are equal",
            "reason": "Core principle",
        })
        result = engine._parse_response(text)
        assert result[0].action_type == "create_proposal"
        assert result[0].params["proposal_type"] == "AddStatement"
        assert result[0].params["proposal_text"] == "All members are equal"

    def test_parse_support_proposal(self):
        engine = DecisionEngine()
        text = json.dumps({
            "action": "support_proposal",
            "proposal_id": "P-abc123def",
            "reason": "Good idea",
        })
        result = engine._parse_response(text)
        assert result[0].action_type == "support_proposal"
        assert result[0].params["proposal_id"] == "P-abc123def"

    def test_parse_comment(self):
        engine = DecisionEngine()
        text = json.dumps({
            "action": "comment",
            "proposal_id": "P-abc123def",
            "comment_text": "I think this is great!",
            "reason": "Want to encourage",
        })
        result = engine._parse_response(text)
        assert result[0].action_type == "comment"
        assert result[0].params["comment_text"] == "I think this is great!"

    def test_build_prompt_contains_rules(self):
        prompt = build_decision_prompt(
            persona_name="Test",
            persona_role="Tester",
            persona_background="Testing agent",
            persona_decision_style="Always test",
            persona_communication_style="Clear and direct",
            persona_trait_summary="balanced",
            community_summary="## Community: Test\nMembers: 5",
            action_history=["[10:00] do_nothing: Observing"],
        )
        assert "KBZ Governance Rules" in prompt
        assert "pulse" in prompt
        assert "AddStatement" in prompt
        assert "Test" in prompt
        assert "Members: 5" in prompt

    def test_build_prompt_omits_failures_block_when_none(self):
        prompt = build_decision_prompt(
            persona_name="A", persona_role="r", persona_background="b",
            persona_decision_style="d", persona_communication_style="c",
            persona_trait_summary="t", community_summary="s",
            action_history=[],
            recent_failures=None,
        )
        assert "Recent Failed Actions" not in prompt
        # Empty list also produces no block — same shape as None.
        prompt2 = build_decision_prompt(
            persona_name="A", persona_role="r", persona_background="b",
            persona_decision_style="d", persona_communication_style="c",
            persona_trait_summary="t", community_summary="s",
            action_history=[], recent_failures=[],
        )
        assert "Recent Failed Actions" not in prompt2

    def test_build_prompt_renders_failures_block(self):
        """The failure detail must reach the LLM verbatim — that's
        the whole point of the feedback loop. Cheap models repeat
        the same invalid call until they're told what was wrong."""
        prompt = build_decision_prompt(
            persona_name="A", persona_role="r", persona_background="b",
            persona_decision_style="d", persona_communication_style="c",
            persona_trait_summary="t", community_summary="s",
            action_history=[],
            recent_failures=[
                "create_proposal: HTTP 422: ChangeVariable on 'PulseSupport' "
                "requires a non-negative value; got '-5'",
                "create_proposal: HTTP 422: ThrowOut target 00000000-… is not "
                "an active member of this community",
            ],
        )
        assert "Recent Failed Actions" in prompt
        assert "DO NOT REPEAT" in prompt
        assert "non-negative value" in prompt
        assert "ThrowOut target" in prompt


# --- Community State Tests ---


class TestCommunitySnapshot:
    def test_snapshot_summarize(self):
        snapshot = CommunitySnapshot(
            community={"name": "Test Kibbutz", "member_count": 3},
            variables={"PulseSupport": "50", "ProposalSupport": "15", "Membership": "50", "ThrowOut": "60", "MaxAge": "2"},
            members=[
                {"user_id": "u1", "seniority": 5},
                {"user_id": "u2", "seniority": 2},
                {"user_id": "u3", "seniority": 0},
            ],
            statements=[{"id": "s1", "statement_text": "We are transparent"}],
            pulses=[{"status": 0, "support_count": 1, "threshold": 2}],
            proposals_out_there=[
                {
                    "id": "p1",
                    "proposal_type": "AddStatement",
                    "proposal_text": "New rule",
                    "user_id": "u1",
                    "support_count": 1,
                    "age": 0,
                }
            ],
        )
        summary = snapshot.summarize(my_user_id="u1", users_cache={"u1": "Alice"})
        assert "Test Kibbutz" in summary
        assert "Members: 3" in summary
        assert "1/2" in summary  # pulse progress
        assert "We are transparent" in summary
        assert "New rule" in summary
        assert "seniority=5" in summary

    def test_snapshot_pulse_progress(self):
        snapshot = CommunitySnapshot(
            community={"name": "X", "member_count": 1},
            pulses=[{"status": 0, "support_count": 3, "threshold": 5}],
        )
        assert snapshot.pulse_support_progress == "3/5"

    def test_snapshot_no_pulse(self):
        snapshot = CommunitySnapshot(
            community={"name": "X", "member_count": 1},
            pulses=[],
        )
        assert snapshot.pulse_support_progress == "no next pulse"


# --- Agent Integration Tests (with real API, mock LLM) ---


class MockDecisionEngine(DecisionEngine):
    """Decision engine that returns predetermined actions for testing."""

    def __init__(self, actions: list[dict] | None = None):
        super().__init__()
        self._actions = actions or [{"action": "do_nothing", "reason": "Testing"}]
        self._call_count = 0

    async def decide(self, **kwargs) -> list[AgentAction]:
        action_data = self._actions[self._call_count % len(self._actions)]
        self._call_count += 1
        params = {k: v for k, v in action_data.items() if k not in ("action", "reason")}
        return [AgentAction(
            action_type=action_data["action"],
            reason=action_data.get("reason", "test"),
            params=params,
        )]


@pytest_asyncio.fixture
async def live_client(db_engine):
    """Client that talks to the real ASGI app (for agent tests)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from kbz.database import get_db
    from kbz.main import app

    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def api_client(live_client):
    """KBZClient backed by the test ASGI app."""
    client = KBZClient.__new__(KBZClient)
    client.base_url = "http://test"
    client._client = live_client
    return client


@pytest_asyncio.fixture
async def community_with_agent(api_client):
    """Create a community with a founder agent ready to act."""
    personas = load_all_personas()
    persona = personas[0]  # Rivka

    engine = MockDecisionEngine()
    agent = Agent(persona=persona, client=api_client, engine=engine)

    # Register
    user = await api_client.create_user(user_name=f"test_{persona.name.lower()}", about="test agent")
    agent.user_id = user["id"]

    # Create community
    community = await api_client.create_community("Test Kibbutz", user["id"])
    agent.community_id = community["id"]

    return agent, community


@pytest.mark.asyncio
async def test_agent_observe(community_with_agent):
    agent, community = community_with_agent
    snapshot = await agent.observe()

    assert snapshot.community_name == "Test Kibbutz"
    assert snapshot.member_count == 1
    assert len(snapshot.members) == 1
    assert snapshot.next_pulse is not None


@pytest.mark.asyncio
async def test_agent_do_nothing(community_with_agent):
    agent, community = community_with_agent
    agent.engine = MockDecisionEngine([
        {"action": "do_nothing", "reason": "Just observing"},
    ])

    logs = await agent.think_and_act()
    log = logs[0]
    assert log.action_type == "do_nothing"
    assert log.success is True
    assert len(agent.action_history) == 1


@pytest.mark.asyncio
async def test_agent_support_pulse(community_with_agent):
    agent, community = community_with_agent

    # Pulse guard: support_pulse is blocked when no proposals exist → do_nothing
    agent.engine = MockDecisionEngine([
        {"action": "support_pulse", "reason": "Let's advance"},
    ])
    logs = await agent.think_and_act()
    assert logs[0].action_type == "do_nothing"  # guard fired: no proposals

    # Create a proposal so pulse support is meaningful
    proposal = await agent.client.create_proposal(
        community["id"], agent.user_id, "AddStatement", "Test"
    )
    await agent.client.submit_proposal(proposal["id"])

    agent.engine = MockDecisionEngine([
        {"action": "support_pulse", "reason": "Now there's a proposal"},
    ])
    logs = await agent.think_and_act()
    assert logs[0].action_type == "support_pulse"
    assert logs[0].success is True


@pytest.mark.asyncio
async def test_agent_create_proposal(community_with_agent):
    agent, community = community_with_agent
    agent.engine = MockDecisionEngine([
        {
            "action": "create_proposal",
            "proposal_type": "AddStatement",
            "proposal_text": "All members shall be transparent",
            "reason": "Core value",
        },
    ])

    logs = await agent.think_and_act()
    log = logs[0]
    assert log.action_type == "create_proposal"
    assert log.success is True
    assert "transparent" in log.details

    # Verify proposal was created and submitted
    proposals = await agent.client.get_proposals(community["id"])
    assert len(proposals) == 1
    assert proposals[0]["proposal_status"] == "OutThere"
    assert proposals[0]["support_count"] == 1  # auto-supported


@pytest.mark.asyncio
async def test_agent_comment_on_proposal(community_with_agent):
    agent, community = community_with_agent

    # First create a proposal to comment on
    proposal = await agent.client.create_proposal(
        community["id"], agent.user_id, "AddStatement", "Test statement"
    )
    await agent.client.submit_proposal(proposal["id"])

    agent.engine = MockDecisionEngine([
        {
            "action": "comment",
            "proposal_id": proposal["id"],
            "comment_text": "I think this is a great idea!",
            "reason": "Supportive of transparency",
        },
    ])

    logs = await agent.think_and_act()
    log = logs[0]
    assert log.action_type == "comment"
    assert log.success is True

    # Verify comment exists
    comments = await agent.client.get_comments("proposal", proposal["id"])
    assert len(comments) == 1
    assert "great idea" in comments[0]["comment_text"]


@pytest.mark.asyncio
async def test_agent_full_cycle(community_with_agent):
    """Test multiple agent actions in sequence: create, comment, support pulse."""
    agent, community = community_with_agent
    agent.engine = MockDecisionEngine([
        # Turn 1: create a proposal
        {
            "action": "create_proposal",
            "proposal_type": "AddStatement",
            "proposal_text": "We value open discussion",
            "reason": "Setting community values",
        },
        # Turn 2: support the pulse to advance
        {"action": "support_pulse", "reason": "Time to move forward"},
        # Turn 3: observe what happened
        {"action": "do_nothing", "reason": "Reviewing the results"},
    ])

    # Run 3 turns
    for _ in range(3):
        await agent.think_and_act()

    assert len(agent.action_history) == 3
    assert agent.action_history[0].action_type == "create_proposal"
    assert agent.action_history[1].action_type == "support_pulse"
    assert agent.action_history[2].action_type == "do_nothing"


@pytest.mark.asyncio
async def test_agent_interview_context(community_with_agent):
    agent, community = community_with_agent
    agent.engine = MockDecisionEngine([
        {"action": "create_proposal", "proposal_type": "AddStatement",
         "proposal_text": "We value transparency", "reason": "Core value"},
    ])

    await agent.think_and_act()
    context = agent.get_interview_context()

    assert agent.persona.name in context
    assert "create_proposal" in context
    assert "Core value" in context


class _CapturingEngine(DecisionEngine):
    """DecisionEngine that records the kwargs decide() was called
    with, so the test can assert recent_failures was passed."""
    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    async def decide(self, **kwargs) -> list[AgentAction]:
        self.calls.append(kwargs)
        return [AgentAction(action_type="do_nothing", reason="captured", params={})]


@pytest.mark.asyncio
async def test_agent_failure_surfaces_in_next_prompt(community_with_agent):
    """End-to-end: an agent's failed create_proposal must appear in
    the recent_failures kwarg the next time decide() is called.

    Why this matters: cheap LLMs (Ollama 8b) hallucinate proposal
    shapes — file ChangeVariable("WrongName") or AddStatement("")
    over and over. Pre-fix the failure was logged as a generic
    "Client error '422 Unprocessable Entity'" and the LLM had no
    way to learn what was wrong. Now the FastAPI `detail` is
    threaded through KBZAPIError → ActionLog.details →
    recent_failures kwarg → prompt block.
    """
    agent, community = community_with_agent
    # First turn: try to file an AddStatement with empty text. Server
    # refuses with 422 (PR #65). The agent's exception handler must
    # capture the rich detail.
    agent.engine = MockDecisionEngine([
        {"action": "create_proposal", "proposal_type": "AddStatement",
         "proposal_text": "", "reason": "should fail — empty text"},
    ])
    logs = await agent.think_and_act()
    assert len(logs) == 1
    assert logs[0].success is False
    assert "non-empty" in logs[0].details.lower(), (
        f"the failure log must carry the FastAPI `detail`, not the "
        f"bare httpx wrapper text; got: {logs[0].details!r}"
    )

    # Second turn: capture what gets fed into decide(). The first
    # turn's failure should appear in recent_failures, ready for
    # the LLM to read.
    cap = _CapturingEngine()
    agent.engine = cap
    await agent.think_and_act()
    assert len(cap.calls) == 1
    failures = cap.calls[0].get("recent_failures") or []
    assert any("non-empty" in line for line in failures), (
        f"the empty-text failure should be in recent_failures so the "
        f"prompt's 'DO NOT REPEAT' block surfaces it to the LLM; got: "
        f"{failures!r}"
    )


# --- LLM preset switcher ---


class TestLLMPresets:
    def test_gpt_oss_20b_nitro_preset_exists(self):
        """The viewer's LLM-switcher dropdown reads LLM_PRESETS to
        populate options. Adding a model to the simulation CLI
        (`--backend openrouter --model openai/gpt-oss-20b:nitro`)
        works without a preset, but the viewer can't switch into it
        unless the preset is registered."""
        from agents.simulation_api import LLM_PRESETS
        assert "or-gpt-oss-20b-nitro" in LLM_PRESETS, (
            "expected the gpt-oss-20b:nitro preset to be registered "
            "so the viewer dropdown picks it up"
        )
        cfg = LLM_PRESETS["or-gpt-oss-20b-nitro"]
        assert cfg["backend"] == "openrouter"
        assert cfg["model"] == "openai/gpt-oss-20b:nitro"
        # `think` controls the Ollama reasoning-mode toggle and is
        # irrelevant for OpenRouter, but the dict shape requires it.
        assert cfg.get("think") is False

    def test_all_presets_have_required_shape(self):
        """Every preset must carry a backend + model — the switcher
        crashes otherwise. Cheap regression for typo-class
        breakage."""
        from agents.simulation_api import LLM_PRESETS
        for name, cfg in LLM_PRESETS.items():
            assert "backend" in cfg, f"{name} missing backend"
            assert "model" in cfg, f"{name} missing model"
            assert cfg["backend"] in ("anthropic", "ollama", "openrouter"), (
                f"{name} has unknown backend {cfg['backend']!r}"
            )


class TestPersonaCount:
    """Pre-fix MAX_MEMBERS was hardcoded to 36 and `build_persona_list`
    clamped silently — `--members 40` got 36, `--members 100` got 36.
    Now we generate procedural names past the curated list."""

    def test_count_below_yaml_returns_yaml_subset(self):
        from agents.persona import build_persona_list, load_all_personas
        n_yaml = len(load_all_personas())
        result = build_persona_list(min(3, n_yaml))
        assert len(result) == min(3, n_yaml)

    def test_count_just_past_yaml_uses_curated_names(self):
        from agents.persona import build_persona_list, load_all_personas, _EXTRA_NAMES
        n_yaml = len(load_all_personas())
        result = build_persona_list(n_yaml + 5)
        assert len(result) == n_yaml + 5
        # The 5 extras must come from the curated _EXTRA_NAMES list
        # (since curated has 30 entries, we won't exhaust it at +5).
        added = result[n_yaml:]
        assert all(p.name in _EXTRA_NAMES for p in added)

    def test_count_far_past_curated_uses_procedural_names(self):
        """The bug we're fixing: 40 used to be capped at 36."""
        from agents.persona import build_persona_list
        result = build_persona_list(40)
        assert len(result) == 40, "previously clamped silently to 36"

    def test_count_100_works(self):
        from agents.persona import build_persona_list
        result = build_persona_list(100)
        assert len(result) == 100
        # All names must be unique — duplicate user_names break the
        # /users registration path (409 collision).
        names = [p.name for p in result]
        assert len(set(names)) == 100, "duplicate names in 100-bot persona list"

    def test_clamp_minimum_is_2(self):
        from agents.persona import build_persona_list
        result = build_persona_list(0)
        assert len(result) == 2
        result = build_persona_list(1)
        assert len(result) == 2


class TestProposalIdResolver:
    """Cheap LLMs (lunaris-8b) emit proposal_ids with extra noise:
    `"id=61d3b594"`, `"61d3b594..."`, `"proposal 61d3b594"`. The
    pre-fix strict regex rejected all of those (13+ failures in 6000
    prod log lines). The new resolver extracts hex runs and matches.
    """

    def _agent(self):
        from agents.persona import Persona, Traits
        from agents.api_client import KBZClient
        engine = MockDecisionEngine()
        persona = Persona(
            name="Test", role="r", background="b", decision_style="d",
            communication_style="c", traits=Traits(),
        )
        return Agent(persona=persona, client=KBZClient(), engine=engine)

    def _snapshot_with_proposal(self, full_id):
        # CommunitySnapshot is a dataclass with default_factory for
        # all collection fields, so passing only what we use here is
        # fine — everything else defaults to empty.
        return CommunitySnapshot(
            proposals_out_there=[{
                "id": full_id, "proposal_type": "AddStatement",
                "proposal_text": "x", "user_id": "u",
            }],
        )

    def test_clean_short_prefix(self):
        agent = self._agent()
        snap = self._snapshot_with_proposal("61d3b594-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_proposal_id("61d3b594", snap) == "61d3b594-f7b6-4afb-95b6-ad129986b7f3"

    def test_id_equals_prefix(self):
        agent = self._agent()
        snap = self._snapshot_with_proposal("61d3b594-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_proposal_id("id=61d3b594", snap) == "61d3b594-f7b6-4afb-95b6-ad129986b7f3"

    def test_trailing_dots(self):
        agent = self._agent()
        snap = self._snapshot_with_proposal("61d3b594-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_proposal_id("61d3b594...", snap) == "61d3b594-f7b6-4afb-95b6-ad129986b7f3"

    def test_word_prefix(self):
        agent = self._agent()
        snap = self._snapshot_with_proposal("61d3b594-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_proposal_id("proposal 61d3b594", snap) == "61d3b594-f7b6-4afb-95b6-ad129986b7f3"

    def test_parens_wrapper(self):
        agent = self._agent()
        snap = self._snapshot_with_proposal("61d3b594-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_proposal_id("(61d3b594)", snap) == "61d3b594-f7b6-4afb-95b6-ad129986b7f3"

    def test_no_match_returns_empty(self):
        agent = self._agent()
        snap = self._snapshot_with_proposal("61d3b594-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_proposal_id("ffffffff", snap) == ""

    def test_proposal_type_name_rejected(self):
        agent = self._agent()
        snap = self._snapshot_with_proposal("61d3b594-f7b6-4afb-95b6-ad129986b7f3")
        # AddStatement is a known type name — must NOT silently
        # match a proposal even if its prefix overlaps.
        assert agent._resolve_proposal_id("AddStatement", snap) == ""

    def test_full_uuid_returned_as_is(self):
        agent = self._agent()
        full = "abcdef12-3456-7890-abcd-ef1234567890"
        snap = self._snapshot_with_proposal(full)
        assert agent._resolve_proposal_id(full, snap) == full


class TestValUuidResolver:
    def _agent(self):
        from agents.persona import Persona, Traits
        from agents.api_client import KBZClient
        engine = MockDecisionEngine()
        persona = Persona(
            name="T", role="r", background="b", decision_style="d",
            communication_style="c", traits=Traits(),
        )
        return Agent(persona=persona, client=KBZClient(), engine=engine)

    def _snapshot_with_artifact(self, full_id):
        return CommunitySnapshot(
            container_artifacts={"c1": [{"id": full_id, "title": "A", "content": "x"}]},
        )

    def test_clean_prefix_resolves(self):
        agent = self._agent()
        snap = self._snapshot_with_artifact("abc12345-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_val_uuid("abc12345", snap) == "abc12345-f7b6-4afb-95b6-ad129986b7f3"

    def test_id_equals_prefix_resolves(self):
        agent = self._agent()
        snap = self._snapshot_with_artifact("abc12345-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_val_uuid("id=abc12345", snap) == "abc12345-f7b6-4afb-95b6-ad129986b7f3"

    def test_parens_resolves(self):
        agent = self._agent()
        snap = self._snapshot_with_artifact("abc12345-f7b6-4afb-95b6-ad129986b7f3")
        assert agent._resolve_val_uuid("(abc12345)", snap) == "abc12345-f7b6-4afb-95b6-ad129986b7f3"

    def test_no_match_returns_input_unchanged(self):
        """Preserves the prior permissive behavior — a UUID we don't
        know about (e.g. just-created and not yet in the snapshot) is
        passed through to the API."""
        agent = self._agent()
        snap = self._snapshot_with_artifact("abc12345-f7b6-4afb-95b6-ad129986b7f3")
        unknown = "ffffffff-aaaa-bbbb-cccc-dddddddddddd"
        assert agent._resolve_val_uuid(unknown, snap) == unknown

    def test_full_uuid_returned_as_is(self):
        agent = self._agent()
        full = "deadbeef-cafe-babe-1234-567890abcdef"
        snap = self._snapshot_with_artifact(full)
        assert agent._resolve_val_uuid(full, snap) == full


@pytest.mark.asyncio
async def test_comment_missing_proposal_id_explains_specifically(community_with_agent):
    """Pre-fix comment failures all returned "Missing proposal_id
    or text" no matter which was actually missing. PR #72 introduced
    the recent_failures feedback block — it can only help if the
    failure detail names which field is wrong."""
    agent, _ = community_with_agent
    agent.engine = MockDecisionEngine([
        {"action": "comment", "comment_text": "great idea", "reason": "y"},
    ])
    logs = await agent.think_and_act()
    assert logs[0].success is False
    assert "proposal_id" in logs[0].details.lower()


@pytest.mark.asyncio
async def test_comment_missing_text_explains_specifically(community_with_agent):
    agent, _ = community_with_agent
    agent.engine = MockDecisionEngine([
        {"action": "comment", "proposal_id": "abc12345", "reason": "y"},
    ])
    logs = await agent.think_and_act()
    assert logs[0].success is False
    assert "comment_text" in logs[0].details.lower()


@pytest.mark.asyncio
async def test_comment_unresolvable_proposal_id_explains(community_with_agent):
    agent, _ = community_with_agent
    agent.engine = MockDecisionEngine([
        {"action": "comment", "proposal_id": "fffffffff",
         "comment_text": "x", "reason": "y"},
    ])
    logs = await agent.think_and_act()
    assert logs[0].success is False
    assert "couldn't resolve" in logs[0].details.lower() or "proposal_id" in logs[0].details.lower()


@pytest.mark.asyncio
async def test_observe_caps_recent_accepted_for_long_running_communities(community_with_agent):
    """Pre-fix observe() fetched ALL proposals via get_proposals() and
    partitioned client-side, so a long-running community with
    hundreds of accepted/rejected rows ballooned the snapshot.
    Lunaris-8b at 8k context crashed; gemma was slowed materially.

    Now each status fetch is explicitly capped. Verify by accepting
    >10 proposals and checking recent_accepted ≤ 20."""
    agent, community = community_with_agent
    user_id = agent.user_id
    cid = community["id"]

    # Land 12 AddStatement proposals.
    for i in range(12):
        r = await agent.client.create_proposal(
            community_id=cid, user_id=user_id,
            proposal_type="AddStatement",
            proposal_text=f"observe-bound statement {i}",
        )
        await agent.client.submit_proposal(r["id"])
        await agent.client.support_proposal(r["id"], user_id)
        # Drive 2 pulses so it lands.
        for _ in range(2):
            await agent.client.support_pulse(cid, user_id)

    snap = await agent.observe()
    assert len(snap.recent_accepted) <= 20, (
        f"recent_accepted should be capped; got {len(snap.recent_accepted)}"
    )


def test_prompt_teaches_add_action_val_uuid_shortcut():
    """AddAction now accepts an optional val_uuid that auto-delegates a
    parent artifact on accept (PR #90 — already shipped to prod). Pre-fix
    the prompt only described the slow two-step (AddAction then
    DelegateArtifact), so bots rarely or never used the shortcut and
    routinely created bare actions with no work to do — exactly the
    "actions are starving" symptom the user reported. This test pins the
    new wording so a future refactor doesn't silently drop the shortcut
    from both the rules and the worked example."""
    prompt = build_decision_prompt(
        persona_name="Test", persona_role="r", persona_background="b",
        persona_decision_style="d", persona_communication_style="c",
        persona_trait_summary="t", community_summary="s",
        action_history=[],
    )
    # The rules block must explicitly call out the val_uuid shortcut on
    # AddAction (not just describe DelegateArtifact separately).
    assert "AddAction" in prompt and "val_uuid" in prompt
    # The mention must associate val_uuid with AddAction specifically —
    # easy proxy: the words appear in the same ~400-char window.
    add_action_idx = prompt.find("AddAction")
    while add_action_idx != -1:
        window = prompt[add_action_idx:add_action_idx + 400]
        if "val_uuid" in window and ("delegate" in window.lower() or "auto-delegat" in window.lower()):
            break
        add_action_idx = prompt.find("AddAction", add_action_idx + 1)
    else:
        raise AssertionError(
            "Prompt mentions AddAction and val_uuid but never associates "
            "them — the one-step val_uuid shortcut isn't taught."
        )
    # The worked example block at the bottom should demonstrate the
    # one-step form (AddAction with val_uuid) rather than the slow
    # AddAction + DelegateArtifact pair. Cheap LLMs lean heavily on the
    # examples; if the example shows two steps they emit two steps.
    assert '"proposal_type": "AddAction"' in prompt
    # Header is "Examples (note about FAKE ids):" since the m4-tuning
    # cycles — match on the prefix, not the exact "Examples:" form.
    examples_block = prompt[prompt.find("Examples"):]
    assert '"proposal_type": "AddAction"' in examples_block, (
        "examples block should include an AddAction example"
    )
    # In the AddAction example specifically, val_uuid must appear so the
    # model sees the one-step pattern modelled.
    aa_in_examples = examples_block.find('"proposal_type": "AddAction"')
    aa_window = examples_block[aa_in_examples:aa_in_examples + 600]
    assert '"val_uuid"' in aa_window, (
        "AddAction example must include val_uuid to demonstrate the "
        "one-step shortcut; otherwise bots will keep emitting the slow "
        "two-step AddAction + DelegateArtifact pattern."
    )


class TestStripJsonComments:
    """Mistral / OpenRouter / many self-hosted models annotate JSON
    values with `// ...` comments — most often after a comma:

        "val_uuid": "319f0559",  // The Plan artifact in Root container

    The previous regex required the char before the whitespace to be
    `]`, `}`, `"`, `'`, or a digit, so the very common `,  //` case
    slipped through and EVERY commented response failed to parse. The
    string-aware stripper walks the text once, tracking string state
    so a literal `//` inside a string value (e.g. a URL) survives."""

    def test_strips_line_comment_after_comma(self):
        """The exact failure shape from prod logs (mistral-small-creative)."""
        from agents.decision_engine import _strip_json_comments
        import json
        src = '[{"val_uuid": "319f0559",  // The Plan artifact in Root\n"reason": "x"}]'
        out = _strip_json_comments(src)
        assert "//" not in out  # comment fully removed
        # And the result must be valid JSON.
        parsed = json.loads(out)
        assert parsed[0]["val_uuid"] == "319f0559"
        assert parsed[0]["reason"] == "x"

    def test_preserves_double_slash_inside_string(self):
        """A URL inside a JSON value contains // and MUST survive."""
        from agents.decision_engine import _strip_json_comments
        import json
        src = '{"url": "https://kibbutznik.org/path", "k": 1}'
        out = _strip_json_comments(src)
        parsed = json.loads(out)
        assert parsed["url"] == "https://kibbutznik.org/path"

    def test_strips_block_comment(self):
        from agents.decision_engine import _strip_json_comments
        import json
        src = '[{"a": 1, /* trailing note */ "b": 2}]'
        out = _strip_json_comments(src)
        parsed = json.loads(out)
        assert parsed == [{"a": 1, "b": 2}]

    def test_handles_escaped_quote_in_string(self):
        """Escapes don't break string-state tracking."""
        from agents.decision_engine import _strip_json_comments
        import json
        src = r'{"k": "he said \"hi\"", "n": 1}  // tail'
        out = _strip_json_comments(src)
        parsed = json.loads(out)
        assert parsed == {"k": 'he said "hi"', "n": 1}

    def test_full_failing_response_from_prod(self):
        """Reproduces the verbatim mistral-small-creative response that
        failed to parse in prod (Apr 28 logs). Pre-fix this returned
        empty/None and the agent fell back to do_nothing — three rounds
        of every agent doing nothing because the stripper missed the
        ',  //' shape."""
        from agents.decision_engine import _strip_json_comments
        import json
        src = '''[
    {
        "action": "create_proposal",
        "proposal_type": "AddAction",
        "val_text": "Marketing Strategy Team",
        "val_uuid": "319f0559",  // The Plan artifact in Root container
        "reason": "Pairing this AddAction with the Plan artifact ensures the team is born with a clear mandate.",
        "eagerness": 10,
        "eager_front": "produce"
    },
    {
        "action": "support_pulse",
        "reason": "Lock in acceptance now.",
        "eagerness": 10,
        "eager_front": "pulse"
    }
]'''
        parsed = json.loads(_strip_json_comments(src))
        assert len(parsed) == 2
        assert parsed[0]["proposal_type"] == "AddAction"
        assert parsed[0]["val_uuid"] == "319f0559"
        assert parsed[1]["action"] == "support_pulse"


class TestIdPrefixes:
    """Single-letter prefixes (P-/A-/C-/K-/S-/U-/M-) tag every id the
    LLM sees, so it can't confuse a proposal_id for an artifact_id (the
    top failure mode in prod: 39 'EditArtifact skipped: artifact <id>
    not found' events in 40 minutes were the model passing a P-… where
    an A-… was needed).

    The renderer (community_state.summarize) wraps every id slice with
    its kind prefix; the agent resolvers strip the prefix before
    matching against snapshot ids."""

    def test_tag_id_renders_with_prefix(self):
        from agents.community_state import tag_id
        assert tag_id("proposal", "abc12345-1234-5678-9012-abcdef123456") == "P-abc12345"
        assert tag_id("artifact", "abc12345-...") == "A-abc12345"
        assert tag_id("container", "abc12345-...") == "C-abc12345"
        assert tag_id("action", "abc12345-...") == "K-abc12345"
        assert tag_id("statement", "abc12345-...") == "S-abc12345"
        assert tag_id("user", "abc12345-...") == "U-abc12345"
        assert tag_id("comment", "abc12345-...") == "M-abc12345"

    def test_strip_id_tag_round_trip(self):
        from agents.community_state import tag_id, strip_id_tag
        full = "abc12345-1234-5678-9012-abcdef123456"
        for kind in ("proposal", "artifact", "container", "action",
                     "statement", "user", "comment"):
            tagged = tag_id(kind, full)
            assert strip_id_tag(tagged) == full[:8]

    def test_strip_id_tag_passes_through_when_unprefixed(self):
        """Backward-compat: a raw id without a prefix is unchanged."""
        from agents.community_state import strip_id_tag
        assert strip_id_tag("abc12345") == "abc12345"
        assert strip_id_tag("abc12345-1234-5678-9012-abcdef123456") == "abc12345-1234-5678-9012-abcdef123456"
        assert strip_id_tag("") == ""

    def test_resolve_proposal_id_handles_tagged_input(self):
        """Agent resolvers must accept `P-abc12345` and resolve it
        against the proposal pool exactly like the bare prefix."""
        from agents.persona import Persona, Traits
        from agents.api_client import KBZClient
        engine = MockDecisionEngine()
        persona = Persona(name="T", role="r", background="b", decision_style="d",
                          communication_style="c", traits=Traits())
        agent = Agent(persona=persona, client=KBZClient(), engine=engine)
        full = "61d3b594-f7b6-4afb-95b6-ad129986b7f3"
        snap = CommunitySnapshot(
            proposals_out_there=[{"id": full, "proposal_type": "AddStatement",
                                  "proposal_text": "x", "user_id": "u"}],
        )
        # Tagged form resolves
        assert agent._resolve_proposal_id("P-61d3b594", snap) == full
        # Untagged form still resolves (backward-compat)
        assert agent._resolve_proposal_id("61d3b594", snap) == full

    def test_resolve_val_uuid_handles_tagged_input(self):
        """Same shape for val_uuid resolver — tagged input is stripped
        and matched against the catch-all pool."""
        from agents.persona import Persona, Traits
        from agents.api_client import KBZClient
        engine = MockDecisionEngine()
        persona = Persona(name="T", role="r", background="b", decision_style="d",
                          communication_style="c", traits=Traits())
        agent = Agent(persona=persona, client=KBZClient(), engine=engine)
        full = "abc12345-1234-5678-9012-abcdef123456"
        snap = CommunitySnapshot(
            container_artifacts={"c1": [{"id": full, "title": "A", "content": "x"}]},
        )
        assert agent._resolve_val_uuid("A-abc12345", snap) == full
        # Tag of the wrong type is fine — we strip and prefix-match anyway
        # (the LLM may emit the wrong kind; the resolver still finds it
        # if the underlying id matches a known entity).
        assert agent._resolve_val_uuid("P-abc12345", snap) == full

    def test_summary_renders_proposal_and_artifact_ids_with_prefixes(self):
        """Most important: the prompt-facing summary string contains
        prefixed ids so the LLM literally cannot confuse types when
        copy-pasting."""
        full_p = "11111111-1111-1111-1111-111111111111"
        full_a = "22222222-2222-2222-2222-222222222222"
        full_c = "33333333-3333-3333-3333-333333333333"
        snap = CommunitySnapshot(
            community={"id": "c", "name": "Test", "member_count": 1, "status": 1},
            variables={"PulseSupport": "50", "ProposalSupport": "50", "MaxAge": "2"},
            proposals_out_there=[{"id": full_p, "proposal_type": "AddStatement",
                                  "proposal_text": "x", "user_id": "u",
                                  "support_count": 0, "age": 0}],
            containers=[{"id": full_c, "title": "Root", "status": 1, "mission": "m"}],
            container_artifacts={full_c: [
                {"id": full_a, "title": "Onboarding", "content": "",
                 "author_user_id": "u", "is_plan": False},
            ]},
        )
        out = snap.summarize(my_user_id="u")
        # Proposal id appears with P- prefix, never as bare slice.
        assert "P-11111111" in out
        assert "id: 11111111" not in out  # bare slice should NOT appear
        # Artifact id appears with A- prefix.
        assert "A-22222222" in out
        # Container id appears with C- prefix.
        assert "C-33333333" in out


class TestEditArtifactDedup:
    """The dominant new failure after PR #96 was 5+ agents racing to file
    competing EditArtifacts on the same artifact in the same pulse —
    each one a real working id, but only one can win at execution time
    and the rest 409. The pre-flight now detects an in-flight
    EditArtifact for the same val_uuid and:
      - if the existing one was filed by another user, auto-supports it
        (so the turn isn't wasted)
      - returns do_nothing if support fails or the existing is the
        agent's own
    Plus the snapshot now flags the in-flight edit prominently next to
    the artifact so the LLM sees it before deciding."""

    def test_snapshot_flags_in_flight_editartifact_on_artifact(self):
        full_a = "11111111-1111-1111-1111-111111111111"
        full_p = "22222222-2222-2222-2222-222222222222"
        full_c = "33333333-3333-3333-3333-333333333333"
        snap = CommunitySnapshot(
            community={"id": "c", "name": "Test", "member_count": 1, "status": 1},
            variables={"PulseSupport": "50", "ProposalSupport": "50", "MaxAge": "2"},
            containers=[{"id": full_c, "title": "Root", "status": 1, "mission": "m"}],
            container_artifacts={full_c: [
                {"id": full_a, "title": "Onboarding", "content": "",
                 "author_user_id": "u", "is_plan": False},
            ]},
            proposals_out_there=[{
                "id": full_p, "proposal_type": "EditArtifact",
                "val_uuid": full_a, "val_text": "Onboarding",
                "proposal_text": "draft content", "user_id": "u-other",
                "support_count": 1, "age": 0,
            }],
        )
        out = snap.summarize(my_user_id="u")
        assert "EditArtifact ALREADY IN FLIGHT" in out
        assert "P-22222222" in out
        assert "SUPPORT THAT proposal, do NOT file a duplicate" in out


class TestSendChatRateLimit:
    """Cap chat to 1 per round (was 2). In prod 86 chats / 16 rounds was
    8.6 chats per round across 6 agents — over-allocated relative to
    productive actions."""

    def test_message_says_max_1(self):
        # Sanity: the rate-limit message in agent.py reflects the cap.
        from pathlib import Path
        src = Path(__file__).parent.parent / "agents" / "agent.py"
        text = src.read_text()
        # The string the user sees in failure logs:
        assert 'Rate limited (max 1 per round)' in text
        assert 'self._chat_this_round >= 1' in text


def test_prompt_disambiguates_update_intention_field_vs_action():
    """The prompt's `update_intention` section is rendered into every
    turn's prompt. Cheap LLMs (lunaris-8b) misread the original
    'optional FIELD' wording as 'optional action' and emitted
    {"action": "update_intention", ...} — which falls through to
    "Unknown action" and wastes the turn (6+ hits in one prod log).

    The fix is in the prompt text, not the dispatcher: an explicit
    ✅/❌ contrast plus the SIDE-FIELD label leaves no room for that
    misread. This test pins the wording so a future edit doesn't
    silently re-introduce the ambiguity."""
    prompt = build_decision_prompt(
        persona_name="Test", persona_role="r", persona_background="b",
        persona_decision_style="d", persona_communication_style="c",
        persona_trait_summary="t", community_summary="s",
        action_history=[],
    )
    # The KBZ_RULES (which contains this section) gets folded into the
    # prompt every turn.
    assert "update_intention" in prompt
    # Must be labeled as NOT an action.
    assert "NOT an action" in prompt or "not an action" in prompt
    # Must call out the wrong shape explicitly so the LLM can pattern-
    # match the warning before emitting it.
    assert '"action": "update_intention"' in prompt
    # Must show the correct attach-to-real-action shape.
    assert "side-field" in prompt.lower() or "sibling field" in prompt.lower()


class TestPulseAlwaysLast:
    """The runtime reorders decisions so `support_pulse` ALWAYS runs
    last in the turn, regardless of where the LLM put it. Pre-fix,
    a pulse emitted first could fire before the agent's
    support_proposal / create_proposal, canceling or accepting the
    proposal it intended to back — wasting the rest of the turn."""

    def test_sort_moves_pulse_to_end(self):
        """The actual sort key used in agent.think_and_act."""
        decisions = [
            AgentAction(action_type="support_pulse", reason="pulse first"),
            AgentAction(action_type="create_proposal", reason="prop"),
            AgentAction(action_type="support_proposal", reason="back x"),
            AgentAction(action_type="comment", reason="discuss"),
        ]
        ordered = sorted(
            decisions,
            key=lambda d: 1 if d.action_type == "support_pulse" else 0,
        )
        assert [d.action_type for d in ordered] == [
            "create_proposal", "support_proposal", "comment", "support_pulse",
        ], "support_pulse must be last; other actions must keep their order"

    def test_sort_is_stable_among_non_pulse_actions(self):
        """Sort must be stable so the LLM's emitted order is preserved
        for everything that isn't `support_pulse` — otherwise a
        DelegateArtifact emitted before its EditArtifact could land
        afterwards and lose its pre-flight context."""
        decisions = [
            AgentAction(action_type="create_proposal", reason="A"),
            AgentAction(action_type="support_pulse", reason="middle"),
            AgentAction(action_type="comment", reason="B"),
            AgentAction(action_type="create_proposal", reason="C"),
        ]
        ordered = sorted(
            decisions,
            key=lambda d: 1 if d.action_type == "support_pulse" else 0,
        )
        reasons = [d.reason for d in ordered]
        assert reasons == ["A", "B", "C", "middle"], reasons

    def test_prompt_documents_reorder(self):
        """The prompt must tell the model the runtime reorders
        support_pulse to last so it doesn't waste reasoning on the
        in-array placement."""
        prompt = build_decision_prompt(
            persona_name="T", persona_role="r", persona_background="b",
            persona_decision_style="d", persona_communication_style="c",
            persona_trait_summary="t", community_summary="s",
            action_history=[],
        )
        assert "support_pulse is ALWAYS last" in prompt
        assert "runtime reorders" in prompt
