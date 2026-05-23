"""Global LLM turn-pacer (agents.decision_engine._pace_turn).

Spaces decision calls >= interval apart across the whole process to calm
the viewer and cap LLM spend. Default 0 = off so dev/tests are unaffected;
these tests set a tiny interval explicitly.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from agents import decision_engine as de


@pytest.fixture
def _reset_pacer():
    """Save/restore the module-global pacer state so these tests don't
    leak an interval into the rest of the suite."""
    prev_interval = de.get_turn_interval()
    prev_last = de._turn_pacer_last
    yield
    de.set_turn_interval(prev_interval)
    de._turn_pacer_last = prev_last


@pytest.mark.asyncio
async def test_pace_turn_is_noop_when_disabled(_reset_pacer):
    de.set_turn_interval(0)
    de._turn_pacer_last = 0.0
    t0 = time.monotonic()
    await de._pace_turn()
    await de._pace_turn()
    assert time.monotonic() - t0 < 0.05  # effectively instant


@pytest.mark.asyncio
async def test_pace_turn_spaces_calls_by_interval(_reset_pacer):
    interval = 0.20
    de.set_turn_interval(interval)
    de._turn_pacer_last = 0.0

    # First call: no prior stamp → returns ~immediately and stamps now.
    await de._pace_turn()
    t_after_first = time.monotonic()
    # Second call: must wait ~interval since the first stamp.
    await de._pace_turn()
    gap = time.monotonic() - t_after_first
    assert gap >= interval * 0.8, f"second call not paced: {gap:.3f}s < {interval}s"


@pytest.mark.asyncio
async def test_pace_turn_serializes_concurrent_callers(_reset_pacer):
    """Three concurrent turns must come out spaced, not all at once."""
    interval = 0.15
    de.set_turn_interval(interval)
    de._turn_pacer_last = 0.0

    stamps: list[float] = []

    async def one():
        await de._pace_turn()
        stamps.append(time.monotonic())

    await asyncio.gather(one(), one(), one())
    stamps.sort()
    # Consecutive stamps should be >= ~interval apart (first is immediate,
    # so check the gaps between the 2nd/3rd).
    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    assert all(g >= interval * 0.8 for g in gaps), f"gaps too small: {gaps}"
