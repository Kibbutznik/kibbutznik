"""Typed async client wrapping all KBZ API endpoints for agent use."""
from typing import Any

import httpx


class KBZAPIError(Exception):
    """4xx/5xx response from the KBZ API, surfaced with the FastAPI
    `detail` field so the agent's failure log carries the *reason*
    (e.g. "ChangeVariable on 'PulseSupport' requires a non-negative
    value") rather than the bare httpx wrapper text ("Client error
    '422 Unprocessable Entity' for url '...'"). The agent's prompt
    folds these into a "Recent failures" block so the LLM can learn
    not to repeat the same mistake — important for cheap local
    models (Ollama 8b) that hallucinate proposal shapes more often
    than Claude does.
    """

    def __init__(self, status_code: int, detail: str, url: str):
        self.status_code = status_code
        self.detail = detail
        self.url = url
        super().__init__(f"HTTP {status_code} on {url}: {detail}")


def _check(resp: httpx.Response) -> None:
    """raise_for_status replacement that extracts FastAPI `detail`.
    Falls back to the response text on parse failure."""
    if resp.is_success:
        return
    detail: str
    try:
        body = resp.json()
        if isinstance(body, dict):
            d = body.get("detail")
            # FastAPI 422 detail is a list of {loc, msg, ...} dicts;
            # collapse them so the agent's log line stays readable.
            if isinstance(d, list):
                detail = "; ".join(
                    str(item.get("msg") or item)
                    for item in d
                ) or str(d)
            else:
                detail = str(d) if d is not None else resp.text
        else:
            detail = str(body)
    except Exception:
        detail = resp.text or f"HTTP {resp.status_code}"
    raise KBZAPIError(
        status_code=resp.status_code,
        detail=detail,
        url=str(resp.request.url) if resp.request else "",
    )


class KBZClient:
    """Agent-facing API client that abstracts HTTP calls into typed methods."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def close(self):
        await self._client.aclose()

    # --- Users ---

    async def create_user(self, user_name: str, password: str = "agent123", about: str = "") -> dict:
        resp = await self._client.post("/users", json={
            "user_name": user_name,
            "password": password,
            "about": about,
        })
        if resp.status_code == 409:
            # Username already exists — reuse the existing account
            return await self.get_user_by_name(user_name)
        _check(resp)
        return resp.json()

    async def get_user(self, user_id: str) -> dict:
        resp = await self._client.get(f"/users/{user_id}")
        _check(resp)
        return resp.json()

    async def get_user_by_name(self, user_name: str) -> dict:
        resp = await self._client.get(f"/users/by-name/{user_name}")
        _check(resp)
        return resp.json()

    # --- Communities ---

    async def create_community(
        self,
        name: str,
        founder_user_id: str,
        initial_artifact_mission: str | None = None,
    ) -> dict:
        payload: dict = {
            "name": name,
            "founder_user_id": founder_user_id,
        }
        if initial_artifact_mission is not None:
            payload["initial_artifact_mission"] = initial_artifact_mission
        resp = await self._client.post("/communities", json=payload)
        _check(resp)
        return resp.json()

    async def get_community(self, community_id: str) -> dict:
        resp = await self._client.get(f"/communities/{community_id}")
        _check(resp)
        return resp.json()

    async def get_variables(self, community_id: str) -> dict[str, str]:
        resp = await self._client.get(f"/communities/{community_id}/variables")
        _check(resp)
        return resp.json()["variables"]

    async def get_children(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/children")
        _check(resp)
        return resp.json()

    # --- Members ---

    async def get_members(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/members")
        _check(resp)
        return resp.json()

    async def get_user_communities(self, user_id: str) -> list[dict]:
        resp = await self._client.get(f"/users/{user_id}/communities")
        _check(resp)
        return resp.json()

    # --- Proposals ---

    async def create_proposal(
        self,
        community_id: str,
        user_id: str,
        proposal_type: str,
        proposal_text: str = "",
        val_uuid: str | None = None,
        val_text: str = "",
        pitch: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "user_id": user_id,
            "proposal_type": proposal_type,
            "proposal_text": proposal_text,
            "val_text": val_text,
        }
        if val_uuid:
            payload["val_uuid"] = val_uuid
        if pitch:
            payload["pitch"] = pitch
        resp = await self._client.post(f"/communities/{community_id}/proposals", json=payload)
        _check(resp)
        return resp.json()

    async def get_proposal(self, proposal_id: str) -> dict:
        resp = await self._client.get(f"/proposals/{proposal_id}")
        _check(resp)
        return resp.json()

    async def get_proposals(
        self,
        community_id: str,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        params = []
        if status:
            params.append(f"status={status}")
        if limit is not None:
            params.append(f"limit={int(limit)}")
        url = f"/communities/{community_id}/proposals"
        if params:
            url += "?" + "&".join(params)
        resp = await self._client.get(url)
        _check(resp)
        return resp.json()

    async def submit_proposal(self, proposal_id: str) -> dict:
        resp = await self._client.patch(f"/proposals/{proposal_id}/submit")
        _check(resp)
        return resp.json()

    async def support_proposal(self, proposal_id: str, user_id: str) -> dict:
        resp = await self._client.post(
            f"/proposals/{proposal_id}/support",
            json={"user_id": user_id},
        )
        if resp.status_code == 409:
            return {"status": "already_supported"}
        _check(resp)
        return resp.json()

    async def unsupport_proposal(self, proposal_id: str, user_id: str) -> dict:
        resp = await self._client.delete(f"/proposals/{proposal_id}/support/{user_id}")
        _check(resp)
        return resp.json()

    # --- Pulses ---

    async def get_pulses(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/pulses")
        _check(resp)
        return resp.json()

    async def support_pulse(self, community_id: str, user_id: str) -> dict:
        resp = await self._client.post(
            f"/communities/{community_id}/pulses/support",
            json={"user_id": user_id},
        )
        if resp.status_code == 409:
            return {"status": "already_supported"}
        _check(resp)
        return resp.json()

    # --- Statements ---

    async def get_statements(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/statements")
        _check(resp)
        return resp.json()

    # --- Actions ---

    async def get_actions(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/actions")
        _check(resp)
        return resp.json()

    # --- Artifacts ---

    async def get_artifact_containers(self, community_id: str) -> list[dict]:
        resp = await self._client.get(
            f"/artifacts/containers/community/{community_id}?include_history=0"
        )
        _check(resp)
        return resp.json()

    async def get_work_tree(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/artifacts/communities/{community_id}/work_tree")
        _check(resp)
        return resp.json()

    async def get_artifact_history(self, artifact_id: str) -> list[dict]:
        """Return the version history of an artifact (newest last)."""
        resp = await self._client.get(f"/artifacts/{artifact_id}/history")
        _check(resp)
        return resp.json()

    # --- Comments ---

    async def add_comment(
        self,
        entity_type: str,
        entity_id: str,
        user_id: str,
        comment_text: str,
        parent_comment_id: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "user_id": user_id,
            "comment_text": comment_text,
        }
        if parent_comment_id:
            payload["parent_comment_id"] = parent_comment_id
        resp = await self._client.post(
            f"/entities/{entity_type}/{entity_id}/comments",
            json=payload,
        )
        _check(resp)
        return resp.json()

    async def get_comments(self, entity_type: str, entity_id: str) -> list[dict]:
        resp = await self._client.get(f"/entities/{entity_type}/{entity_id}/comments")
        _check(resp)
        return resp.json()

    async def vote_comment(self, comment_id: str, delta: int) -> dict:
        resp = await self._client.post(
            f"/comments/{comment_id}/score",
            json={"delta": delta},
        )
        _check(resp)
        return resp.json()
