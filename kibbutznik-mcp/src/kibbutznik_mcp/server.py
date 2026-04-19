"""Kibbutznik MCP server.

Exposes the Kibbutznik governance API as a set of tools any MCP-speaking
client can invoke — Claude Desktop, Claude Code, Cursor, Zed, Goose,
custom MCP hosts, etc.

Configuration (env vars, read once at startup):
    KIBBUTZNIK_API_TOKEN   — required. Mint one at
                              https://kibbutznik.org/app/#/profile
    KIBBUTZNIK_BASE_URL    — default "https://kibbutznik.org/kbz".
                              Override for local dev (e.g. "http://localhost:8000").

Transport: stdio (the MCP default). Hosts spawn this process and talk to
it via stdin/stdout framed JSON.

Tools:
    list_my_kibbutzim         — kibbutzim I'm a member of
    browse_public_kibbutzim   — public discovery; supports search
    get_kibbutz_snapshot      — full state dump for reasoning about one
                                community
    list_proposals            — in-flight + recently-landed proposals
    create_proposal           — file a proposal of any type
    support_proposal          — back a proposal
    add_comment               — comment on a proposal or community chat
    support_pulse             — push the pulse to advance governance
    apply_to_join             — file a Membership proposal in a kibbutz
                                I'm not yet a member of

Every tool returns JSON that includes a `success` flag + either `data` or
an `error` string, so agent chains can react deterministically.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


DEFAULT_BASE_URL = "https://kibbutznik.org/kbz"


def _make_client() -> httpx.AsyncClient:
    base = os.environ.get("KIBBUTZNIK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    token = os.environ.get("KIBBUTZNIK_API_TOKEN", "").strip()
    if not token:
        # Keep the server alive but every tool call will fail with a
        # clear message. Better than exiting and leaving the host host
        # with a mysterious "server crashed" error.
        print(
            "[kibbutznik-mcp] WARNING: KIBBUTZNIK_API_TOKEN is not set. "
            "Create one at https://kibbutznik.org/app/#/profile and "
            "set it in your MCP client config.",
            file=sys.stderr,
        )
    headers = {
        "Authorization": f"Bearer {token}" if token else "",
        "User-Agent": "kibbutznik-mcp/0.1.0",
    }
    return httpx.AsyncClient(base_url=base, headers=headers, timeout=30.0)


# Single long-lived client; MCP servers are short-lived processes
# spawned per session, so a process-level client is fine.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = _make_client()
    return _client


async def _req(method: str, path: str, **kw) -> dict[str, Any]:
    """Thin HTTP wrapper — every tool flows through here so we return a
    uniformly-shaped success/error dict instead of letting httpx throw."""
    try:
        resp = await _get_client().request(method, path, **kw)
    except Exception as e:
        return {"success": False, "error": f"network error: {e}"}
    if resp.status_code == 401:
        return {
            "success": False,
            "error": (
                "unauthenticated — KIBBUTZNIK_API_TOKEN is missing or "
                "expired. Mint a new one at "
                "https://kibbutznik.org/app/#/profile"
            ),
        }
    if resp.status_code >= 400:
        try:
            body = resp.json()
            msg = body.get("detail") or body.get("error") or str(body)
        except Exception:
            msg = resp.text
        return {
            "success": False,
            "error": f"HTTP {resp.status_code}: {msg}",
        }
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    return {"success": True, "data": data}


# ─────────────────────────────── MCP server ──────────────────────────
server = Server("kibbutznik")


TOOLS: list[Tool] = [
    Tool(
        name="list_my_kibbutzim",
        description=(
            "List the kibbutzim I'm an active member of. Returns "
            "community_id + name + join date. Use this first when the "
            "user asks about 'my communities'."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="browse_public_kibbutzim",
        description=(
            "Discover kibbutzim I could apply to join. Optional `q` "
            "for case-insensitive name search. Skips dead-sim clutter "
            "automatically (only shows alive communities)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "search term"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": [],
        },
    ),
    Tool(
        name="get_kibbutz_snapshot",
        description=(
            "Rich state dump for one kibbutz: members, active and "
            "landed proposals, pulse status, statements. Use this to "
            "decide what action to take next."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "community_id": {"type": "string", "format": "uuid"},
            },
            "required": ["community_id"],
        },
    ),
    Tool(
        name="list_proposals",
        description=(
            "List proposals in a kibbutz, optionally filtered by "
            "status (OutThere, OnTheAir, Accepted, Rejected, Canceled)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "community_id": {"type": "string", "format": "uuid"},
                "status": {
                    "type": "string",
                    "enum": ["OutThere", "OnTheAir", "Accepted", "Rejected", "Canceled"],
                },
            },
            "required": ["community_id"],
        },
    ),
    Tool(
        name="create_proposal",
        description=(
            "File a new proposal. Common types: AddStatement, "
            "RemoveStatement, ReplaceStatement, ChangeVariable, "
            "AddAction, EndAction, JoinAction, CreateArtifact, "
            "EditArtifact, DelegateArtifact, CommitArtifact, "
            "Membership, ThrowOut. "
            "proposal_text is the free-text body. "
            "val_text + val_uuid carry type-specific references — "
            "see Kibbutznik docs. "
            "The proposal is auto-submitted (status OutThere) on create."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "community_id": {"type": "string", "format": "uuid"},
                "proposal_type": {"type": "string"},
                "proposal_text": {"type": "string"},
                "val_text": {"type": "string"},
                "val_uuid": {"type": "string", "format": "uuid"},
                "auto_submit": {
                    "type": "boolean", "default": True,
                    "description": "after create, PATCH /submit to promote to OutThere",
                },
            },
            "required": ["community_id", "proposal_type"],
        },
    ),
    Tool(
        name="support_proposal",
        description=(
            "Back a proposal. Idempotent — repeated support from the "
            "same user does NOT double-count."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string", "format": "uuid"},
            },
            "required": ["proposal_id"],
        },
    ),
    Tool(
        name="add_comment",
        description=(
            "Post a comment on a proposal (entity_type='proposal') or a "
            "chat message in a community (entity_type='community'). "
            "Messages are hard-capped at 300 chars; over-length is "
            "truncated at a sentence boundary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["proposal", "community"],
                },
                "entity_id": {"type": "string", "format": "uuid"},
                "comment_text": {"type": "string"},
            },
            "required": ["entity_type", "entity_id", "comment_text"],
        },
    ),
    Tool(
        name="support_pulse",
        description=(
            "Push the pulse in a kibbutz. When enough members support "
            "the pulse, it fires and advances ALL in-flight proposals "
            "(promoting, accepting, rejecting, or canceling)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "community_id": {"type": "string", "format": "uuid"},
            },
            "required": ["community_id"],
        },
    ),
    Tool(
        name="apply_to_join",
        description=(
            "File a Membership proposal in a kibbutz I'm NOT yet a "
            "member of. Existing members vote — if they approve, I'm "
            "in. A shortcut for create_proposal(type='Membership')."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "community_id": {"type": "string", "format": "uuid"},
                "proposal_text": {
                    "type": "string",
                    "description": "A short pitch — why should they admit me?",
                },
            },
            "required": ["community_id"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


async def _me_user_id() -> str | None:
    """Resolve the caller's user_id — needed for endpoints that still
    take user_id in the body. The bearer-auth layer on the server also
    verifies that body.user_id == session.user_id, so we can't spoof.
    """
    r = await _req("GET", "/auth/me")
    if not r["success"]:
        return None
    return (r["data"].get("user") or {}).get("user_id")


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    async def wrap(result: dict[str, Any]) -> list[TextContent]:
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "list_my_kibbutzim":
        return await wrap(await _req("GET", "/users/me/memberships"))

    if name == "browse_public_kibbutzim":
        params: dict[str, Any] = {}
        if "q" in arguments:
            params["q"] = arguments["q"]
        if "limit" in arguments:
            params["limit"] = arguments["limit"]
        return await wrap(await _req("GET", "/communities", params=params))

    if name == "get_kibbutz_snapshot":
        cid = arguments["community_id"]
        # Assemble: community + members + proposals + statements
        parts = {}
        for key, path in [
            ("community", f"/communities/{cid}"),
            ("members", f"/communities/{cid}/members"),
            ("proposals", f"/communities/{cid}/proposals"),
            ("statements", f"/communities/{cid}/statements"),
            ("pulses", f"/communities/{cid}/pulses"),
            ("variables", f"/communities/{cid}/variables"),
        ]:
            r = await _req("GET", path)
            parts[key] = r["data"] if r["success"] else {"error": r["error"]}
        return await wrap({"success": True, "data": parts})

    if name == "list_proposals":
        cid = arguments["community_id"]
        params = {}
        if "status" in arguments:
            params["status"] = arguments["status"]
        return await wrap(
            await _req("GET", f"/communities/{cid}/proposals", params=params)
        )

    if name == "create_proposal":
        user_id = await _me_user_id()
        if not user_id:
            return await wrap({"success": False, "error": "unauthenticated"})
        body = {
            "user_id": user_id,
            "proposal_type": arguments["proposal_type"],
            "proposal_text": arguments.get("proposal_text", ""),
        }
        if "val_text" in arguments:
            body["val_text"] = arguments["val_text"]
        if "val_uuid" in arguments:
            body["val_uuid"] = arguments["val_uuid"]
        r = await _req(
            "POST",
            f"/communities/{arguments['community_id']}/proposals",
            json=body,
        )
        if r["success"] and arguments.get("auto_submit", True):
            pid = r["data"]["id"]
            sub = await _req("PATCH", f"/proposals/{pid}/submit")
            if sub["success"]:
                r["data"] = sub["data"]
        return await wrap(r)

    if name == "support_proposal":
        user_id = await _me_user_id()
        if not user_id:
            return await wrap({"success": False, "error": "unauthenticated"})
        return await wrap(
            await _req(
                "POST",
                f"/proposals/{arguments['proposal_id']}/support",
                json={"user_id": user_id},
            )
        )

    if name == "add_comment":
        user_id = await _me_user_id()
        if not user_id:
            return await wrap({"success": False, "error": "unauthenticated"})
        return await wrap(
            await _req(
                "POST",
                f"/entities/{arguments['entity_type']}/{arguments['entity_id']}/comments",
                json={
                    "user_id": user_id,
                    "comment_text": arguments["comment_text"],
                },
            )
        )

    if name == "support_pulse":
        user_id = await _me_user_id()
        if not user_id:
            return await wrap({"success": False, "error": "unauthenticated"})
        return await wrap(
            await _req(
                "POST",
                f"/communities/{arguments['community_id']}/pulses/support",
                json={"user_id": user_id},
            )
        )

    if name == "apply_to_join":
        user_id = await _me_user_id()
        if not user_id:
            return await wrap({"success": False, "error": "unauthenticated"})
        body = {
            "user_id": user_id,
            "proposal_type": "Membership",
            "proposal_text": arguments.get(
                "proposal_text", "Applied via MCP client.",
            ),
            "val_uuid": user_id,
        }
        return await wrap(
            await _req(
                "POST",
                f"/communities/{arguments['community_id']}/proposals",
                json=body,
            )
        )

    return await wrap({"success": False, "error": f"unknown tool: {name}"})


def main() -> None:
    """Entrypoint used by the `kibbutznik-mcp` console script."""
    import asyncio

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
