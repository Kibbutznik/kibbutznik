"""Typed async client wrapping all KBZ API endpoints for agent use."""
import uuid
from typing import Any

import httpx


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
        resp.raise_for_status()
        return resp.json()

    async def get_user(self, user_id: str) -> dict:
        resp = await self._client.get(f"/users/{user_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_user_by_name(self, user_name: str) -> dict:
        resp = await self._client.get(f"/users/by-name/{user_name}")
        resp.raise_for_status()
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
        resp.raise_for_status()
        return resp.json()

    async def get_community(self, community_id: str) -> dict:
        resp = await self._client.get(f"/communities/{community_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_variables(self, community_id: str) -> dict[str, str]:
        resp = await self._client.get(f"/communities/{community_id}/variables")
        resp.raise_for_status()
        return resp.json()["variables"]

    async def get_children(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/children")
        resp.raise_for_status()
        return resp.json()

    # --- Members ---

    async def get_members(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/members")
        resp.raise_for_status()
        return resp.json()

    async def get_user_communities(self, user_id: str) -> list[dict]:
        resp = await self._client.get(f"/users/{user_id}/communities")
        resp.raise_for_status()
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
    ) -> dict:
        payload: dict[str, Any] = {
            "user_id": user_id,
            "proposal_type": proposal_type,
            "proposal_text": proposal_text,
            "val_text": val_text,
        }
        if val_uuid:
            payload["val_uuid"] = val_uuid
        resp = await self._client.post(f"/communities/{community_id}/proposals", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_proposal(self, proposal_id: str) -> dict:
        resp = await self._client.get(f"/proposals/{proposal_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_proposals(self, community_id: str, status: str | None = None) -> list[dict]:
        url = f"/communities/{community_id}/proposals"
        if status:
            url += f"?status={status}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def submit_proposal(self, proposal_id: str) -> dict:
        resp = await self._client.patch(f"/proposals/{proposal_id}/submit")
        resp.raise_for_status()
        return resp.json()

    async def support_proposal(self, proposal_id: str, user_id: str) -> dict:
        resp = await self._client.post(
            f"/proposals/{proposal_id}/support",
            json={"user_id": user_id},
        )
        if resp.status_code == 409:
            return {"status": "already_supported"}
        resp.raise_for_status()
        return resp.json()

    async def unsupport_proposal(self, proposal_id: str, user_id: str) -> dict:
        resp = await self._client.delete(f"/proposals/{proposal_id}/support/{user_id}")
        resp.raise_for_status()
        return resp.json()

    # --- Pulses ---

    async def get_pulses(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/pulses")
        resp.raise_for_status()
        return resp.json()

    async def support_pulse(self, community_id: str, user_id: str) -> dict:
        resp = await self._client.post(
            f"/communities/{community_id}/pulses/support",
            json={"user_id": user_id},
        )
        if resp.status_code == 409:
            return {"status": "already_supported"}
        resp.raise_for_status()
        return resp.json()

    # --- Statements ---

    async def get_statements(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/statements")
        resp.raise_for_status()
        return resp.json()

    # --- Actions ---

    async def get_actions(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/communities/{community_id}/actions")
        resp.raise_for_status()
        return resp.json()

    # --- Artifacts ---

    async def get_artifact_containers(self, community_id: str) -> list[dict]:
        resp = await self._client.get(
            f"/artifacts/containers/community/{community_id}?include_history=0"
        )
        resp.raise_for_status()
        return resp.json()

    async def get_work_tree(self, community_id: str) -> list[dict]:
        resp = await self._client.get(f"/artifacts/communities/{community_id}/work_tree")
        resp.raise_for_status()
        return resp.json()

    async def get_artifact_history(self, artifact_id: str) -> list[dict]:
        """Return the version history of an artifact (newest last)."""
        resp = await self._client.get(f"/artifacts/{artifact_id}/history")
        resp.raise_for_status()
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
        resp.raise_for_status()
        return resp.json()

    async def get_comments(self, entity_type: str, entity_id: str) -> list[dict]:
        resp = await self._client.get(f"/entities/{entity_type}/{entity_id}/comments")
        resp.raise_for_status()
        return resp.json()

    async def vote_comment(self, comment_id: str, delta: int) -> dict:
        resp = await self._client.post(
            f"/comments/{comment_id}/score",
            json={"delta": delta},
        )
        resp.raise_for_status()
        return resp.json()
