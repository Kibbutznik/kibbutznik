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
        assert "Rivka" in names
        assert "Moshe" in names
        assert "Dana" in names
        assert "Yoav" in names
        assert "Tamar" in names
        assert "Avi" in names

    def test_load_single_persona(self):
        persona_dir = Path(__file__).parent.parent / "agents" / "personas"
        persona = load_persona(str(persona_dir / "progressive.yaml"))
        assert persona.name == "Rivka"
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
            {"action": "support_proposal", "proposal_id": "abc-123", "reason": "reason2"},
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
            "proposal_id": "abc-123",
            "reason": "Good idea",
        })
        result = engine._parse_response(text)
        assert result[0].action_type == "support_proposal"
        assert result[0].params["proposal_id"] == "abc-123"

    def test_parse_comment(self):
        engine = DecisionEngine()
        text = json.dumps({
            "action": "comment",
            "proposal_id": "abc-123",
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
