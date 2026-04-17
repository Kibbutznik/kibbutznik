"""Dual-write tests — memory_extractor fires agent.memory_extracted events.

We assert the events land on the bus with the right shape (so the ingestor
handler, tested separately in test_tkg_ingestor.py, can consume them), and
that flipping `tkg_dual_write` to False silences them.

We also cover the comment-length truncation helper in agents.agent, since
that rule only holds if the code enforces it independent of the LLM.
"""

from __future__ import annotations

import uuid

import pytest

from agents.agent import _truncate_comment
from agents.memory_extractor import MemoryExtractor
from kbz.config import settings
from kbz.services.event_bus import event_bus


class _FakeStore:
    """Stub MemoryStore — records adds without touching the DB."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def add(self, **kwargs) -> None:
        self.calls.append(kwargs)

    # Unused by the tests below but the extractor's other methods may call these
    async def update(self, *a, **kw) -> None:
        pass

    async def get_relationship_with(self, *a, **kw):
        return None


async def _capture_events(n: int) -> list:
    """Subscribe briefly, collect up to n events already sitting on the queue."""
    q = event_bus.subscribe()
    out = []
    try:
        while len(out) < n:
            try:
                out.append(q.get_nowait())
            except Exception:
                break
    finally:
        try:
            event_bus.unsubscribe(q)
        except ValueError:
            pass
    return out


@pytest.mark.asyncio
async def test_dual_write_emits_when_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "tkg_dual_write", True)
    extractor = MemoryExtractor(_FakeStore())
    extractor._turn_community_id = str(uuid.uuid4())

    user_id = str(uuid.uuid4())
    proposal_id = str(uuid.uuid4())

    # Subscribe BEFORE emission so the event is in our queue
    q = event_bus.subscribe()
    try:
        await extractor._add(
            user_id=user_id,
            memory_type="goal",
            content="Working on the onboarding artifact",
            importance=0.7,
            category="artifact_work",
            round_num=5,
            related_id=proposal_id,
        )
        # Drain the queue
        events = []
        while not q.empty():
            events.append(q.get_nowait())
    finally:
        event_bus.unsubscribe(q)

    memory_events = [e for e in events if e.event_type == "agent.memory_extracted"]
    assert len(memory_events) == 1, f"expected 1 agent.memory_extracted, got {len(memory_events)}"
    evt = memory_events[0]
    assert evt.data["memory_type"] == "goal"
    assert evt.data["related_id"] == proposal_id
    assert "onboarding artifact" in evt.data["content"]
    assert evt.data["round_num"] == 5
    assert str(evt.user_id) == user_id


@pytest.mark.asyncio
async def test_dual_write_silent_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "tkg_dual_write", False)
    extractor = MemoryExtractor(_FakeStore())
    extractor._turn_community_id = str(uuid.uuid4())

    q = event_bus.subscribe()
    try:
        await extractor._add(
            user_id=str(uuid.uuid4()),
            memory_type="episodic",
            content="supported their proposal",
            importance=0.3,
            category="social",
            round_num=5,
            related_id=str(uuid.uuid4()),
        )
        events = []
        while not q.empty():
            events.append(q.get_nowait())
    finally:
        event_bus.unsubscribe(q)

    memory_events = [e for e in events if e.event_type == "agent.memory_extracted"]
    assert memory_events == [], "dual-write should NOT emit when tkg_dual_write is False"


@pytest.mark.asyncio
async def test_add_wrapper_still_writes_to_legacy_store(monkeypatch):
    """Even with the dual-write layer, legacy MemoryStore.add() is called."""
    monkeypatch.setattr(settings, "tkg_dual_write", False)
    store = _FakeStore()
    extractor = MemoryExtractor(store)
    extractor._turn_community_id = None

    await extractor._add(
        user_id=str(uuid.uuid4()),
        memory_type="relationship",
        content="ally — frequently support their proposals",
        importance=0.4,
        category="social",
        round_num=2,
        related_id=str(uuid.uuid4()),
    )
    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["memory_type"] == "relationship"
    assert "ally" in call["content"]


def test_truncate_comment_short_untouched():
    short = "Love the new opening line — sharper than the original."
    assert _truncate_comment(short) == short


def test_truncate_comment_long_is_clamped():
    long = (
        "This is a very long comment that agents would love to write given the "
        "opportunity, full of flowery editor-in-a-writers-room prose, ruminations "
        "on craft, and gentle but condescending suggestions about how the author "
        "might consider revising their draft in ways that honor both voice and clarity, "
        "because governance is a conversation and every word matters."
    )
    out = _truncate_comment(long)
    assert len(out) <= 310  # cap is 300, ellipsis adds a char
    assert out.endswith("…")
    # Prefer a sentence-boundary break when one is within the cap window
    assert "agents would love to write" in out


def test_truncate_comment_hard_cut_fallback():
    """When the whole cap window is one unbroken run, hard-cut + ellipsis."""
    blob = "x" * 500
    out = _truncate_comment(blob)
    assert len(out) <= 310
    assert out.endswith("…")


def test_truncate_comment_empty_untouched():
    assert _truncate_comment("") == ""
    assert _truncate_comment(None) is None or _truncate_comment(None) == ""
