"""Unit tests for the kibbutznik-mcp server.

Doesn't spin up stdio. Just checks:
  - The TOOLS list is well-formed (names unique, schemas valid).
  - `_req` maps HTTP failures to the {success,error} envelope we
    promise every tool consumer — 401 vs other 4xx vs network.

The full HTTP flow is already covered by our live smoke test in the
README — no value in faking the entire FastAPI app here.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kibbutznik_mcp.server import TOOLS, _req, server


# ── Tool shape ─────────────────────────────────────────────────────

def test_tool_names_are_unique():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names)), f"duplicate tool name in {names}"


def test_tool_names_match_server_expectation():
    # The 9 tools the README advertises. If one disappears/renames the
    # README must change too.
    expected = {
        "list_my_kibbutzim",
        "browse_public_kibbutzim",
        "get_kibbutz_snapshot",
        "list_proposals",
        "create_proposal",
        "support_proposal",
        "add_comment",
        "support_pulse",
        "apply_to_join",
    }
    actual = {t.name for t in TOOLS}
    assert actual == expected


def test_every_tool_has_description_and_schema():
    for t in TOOLS:
        assert t.description and len(t.description) > 20, f"{t.name}: thin description"
        assert isinstance(t.inputSchema, dict), f"{t.name}: missing inputSchema"
        assert t.inputSchema.get("type") == "object"
        # `required` must only name fields declared in `properties`
        props = set(t.inputSchema.get("properties", {}).keys())
        reqd = set(t.inputSchema.get("required", []))
        assert reqd <= props, f"{t.name}: required {reqd - props} not in properties"


def test_server_name_is_kibbutznik():
    assert server.name == "kibbutznik"


# ── _req envelope ──────────────────────────────────────────────────

def _mock_response(status: int, body) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body)
    r.text = json.dumps(body) if not isinstance(body, str) else body
    return r


@pytest.mark.asyncio
async def test_req_2xx_returns_success_with_data():
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=_mock_response(200, {"x": 1}))
    with patch("kibbutznik_mcp.server._get_client", return_value=mock_client):
        r = await _req("GET", "/anything")
    assert r == {"success": True, "data": {"x": 1}}


@pytest.mark.asyncio
async def test_req_401_returns_clear_auth_error():
    """Auth failures should direct the user to mint a new token —
    that's a more actionable error than 'HTTP 401'."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        return_value=_mock_response(401, {"detail": "expired"})
    )
    with patch("kibbutznik_mcp.server._get_client", return_value=mock_client):
        r = await _req("GET", "/anything")
    assert r["success"] is False
    assert "unauthenticated" in r["error"].lower()
    assert "kibbutznik.org/app" in r["error"]  # link to token management


@pytest.mark.asyncio
async def test_req_other_4xx_surfaces_server_detail():
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        return_value=_mock_response(
            400, {"detail": "orientation must be one of [...]"}
        )
    )
    with patch("kibbutznik_mcp.server._get_client", return_value=mock_client):
        r = await _req("POST", "/anything")
    assert r["success"] is False
    assert "400" in r["error"]
    assert "orientation must be one of" in r["error"]


@pytest.mark.asyncio
async def test_req_network_error_caught():
    mock_client = MagicMock()
    mock_client.request = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("kibbutznik_mcp.server._get_client", return_value=mock_client):
        r = await _req("GET", "/anything")
    assert r["success"] is False
    assert "network error" in r["error"].lower()


@pytest.mark.asyncio
async def test_req_non_json_body_still_envelopes():
    """If the server returns HTML or garbled text, we shouldn't raise
    — return `data.raw=<text>` so the agent can decide what to do."""
    mock_client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(side_effect=ValueError("not json"))
    resp.text = "<html>oops</html>"
    mock_client.request = AsyncMock(return_value=resp)
    with patch("kibbutznik_mcp.server._get_client", return_value=mock_client):
        r = await _req("GET", "/anything")
    assert r["success"] is True
    assert r["data"] == {"raw": "<html>oops</html>"}
