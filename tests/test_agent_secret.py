"""Cookieless-impersonation gate (KBZ_AGENT_API_SECRET).

Pre-fix, any anonymous caller could POST {"user_id": "<victim>"} with no
cookie and act as that victim, because enforce_session_matches_body
trusted every cookieless request. The gate closes that hole when a
secret is configured, while staying permissive (legacy behavior) when
it isn't — so dev/test/existing-prod don't break until an operator
opts in.

Uses POST /communities (founder_user_id flows through
enforce_session_matches_body) as the representative cookieless write.
"""

from __future__ import annotations

import pytest

from kbz.config import settings
from tests.conftest import create_test_user


@pytest.mark.asyncio
async def test_cookieless_write_allowed_when_secret_disabled(client):
    """Default config (secret empty): legacy permissive path — a
    cookieless write with a body user_id still works."""
    user = await create_test_user(client)
    resp = await client.post(
        "/communities", json={"name": "open", "founder_user_id": user["id"]}
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_cookieless_write_rejected_when_secret_set_and_header_absent(
    client, monkeypatch
):
    user = await create_test_user(client)
    monkeypatch.setattr(settings, "agent_api_secret", "s3cr3t-agent-token")
    resp = await client.post(
        "/communities", json={"name": "blocked", "founder_user_id": user["id"]}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cookieless_write_rejected_with_wrong_header(client, monkeypatch):
    user = await create_test_user(client)
    monkeypatch.setattr(settings, "agent_api_secret", "s3cr3t-agent-token")
    resp = await client.post(
        "/communities",
        json={"name": "blocked2", "founder_user_id": user["id"]},
        headers={"X-KBZ-Agent-Secret": "not-the-secret"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cookieless_write_allowed_with_correct_agent_header(client, monkeypatch):
    user = await create_test_user(client)
    monkeypatch.setattr(settings, "agent_api_secret", "s3cr3t-agent-token")
    resp = await client.post(
        "/communities",
        json={"name": "trusted-agent", "founder_user_id": user["id"]},
        headers={"X-KBZ-Agent-Secret": "s3cr3t-agent-token"},
    )
    assert resp.status_code == 201
