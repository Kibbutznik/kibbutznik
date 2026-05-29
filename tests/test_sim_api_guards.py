"""Access guards on the simulation control surface (agents/simulation_api.py).

The dangerous /simulation/* writes (restart=DB wipe, llm=switch model,
run-round, chat) are gated to operators via the X-KBZ-Agent-Secret header.
This pins that gate without needing a live orchestrator.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agents.simulation_api import _require_operator
from kbz.config import settings


def _req(secret_header=None):
    headers = {}
    if secret_header is not None:
        headers["X-KBZ-Agent-Secret"] = secret_header
    return SimpleNamespace(headers=headers)


def test_operator_gate_permissive_when_no_secret(monkeypatch):
    """Dev/test: with no secret configured, the gate is permissive
    (matches enforce_session_matches_body's fallback)."""
    monkeypatch.setattr(settings, "agent_api_secret", "")
    _require_operator(_req())  # must not raise


def test_operator_gate_blocks_when_secret_set_no_header(monkeypatch):
    monkeypatch.setattr(settings, "agent_api_secret", "s3cr3t")
    with pytest.raises(HTTPException) as e:
        _require_operator(_req())
    assert e.value.status_code == 403


def test_operator_gate_blocks_wrong_header(monkeypatch):
    monkeypatch.setattr(settings, "agent_api_secret", "s3cr3t")
    with pytest.raises(HTTPException) as e:
        _require_operator(_req("nope"))
    assert e.value.status_code == 403


def test_operator_gate_allows_correct_header(monkeypatch):
    monkeypatch.setattr(settings, "agent_api_secret", "s3cr3t")
    _require_operator(_req("s3cr3t"))  # must not raise
