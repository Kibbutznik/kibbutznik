"""Agent-side memory store — wraps HTTP calls to the KBZ memory API."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MemoryStore:
    """Provides typed async access to the agent_memories table via the KBZ API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    # ---- write ----

    async def add(
        self,
        user_id: str,
        memory_type: str,
        content: str,
        importance: float = 0.5,
        category: str | None = None,
        round_num: int | None = None,
        related_id: str | None = None,
        expires_at: int | None = None,
    ) -> dict:
        """Create a new memory record."""
        payload: dict[str, Any] = {
            "user_id": user_id,
            "memory_type": memory_type,
            "content": content,
            "importance": importance,
        }
        if category:
            payload["category"] = category
        if round_num is not None:
            payload["round_num"] = round_num
        if related_id:
            payload["related_id"] = related_id
        if expires_at is not None:
            payload["expires_at"] = expires_at

        try:
            resp = await self._client.post("/memories", json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"[MemoryStore] Failed to add memory: {e}")
            return {}

    async def update(self, memory_id: str, **fields: Any) -> dict:
        """Update fields of an existing memory."""
        try:
            resp = await self._client.put(f"/memories/{memory_id}", json=fields)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"[MemoryStore] Failed to update memory {memory_id}: {e}")
            return {}

    # ---- read ----

    async def get_recent(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Get most recent memories (newest first)."""
        params: dict[str, Any] = {"limit": limit, "order_by": "recent"}
        if memory_type:
            params["memory_type"] = memory_type
        try:
            resp = await self._client.get(f"/memories/{user_id}", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"[MemoryStore] Failed to get recent memories: {e}")
            return []

    async def get_top(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 5,
        min_importance: float = 0.0,
    ) -> list[dict]:
        """Get highest-importance memories."""
        params: dict[str, Any] = {
            "limit": limit,
            "order_by": "importance",
            "min_importance": min_importance,
        }
        if memory_type:
            params["memory_type"] = memory_type
        try:
            resp = await self._client.get(f"/memories/{user_id}", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"[MemoryStore] Failed to get top memories: {e}")
            return []

    async def get_goals(self, user_id: str, active_only: bool = True) -> list[dict]:
        """Get goal memories. Active goals have importance > 0."""
        params: dict[str, Any] = {
            "memory_type": "goal",
            "limit": 10,
            "order_by": "importance",
        }
        if active_only:
            params["min_importance"] = 0.01  # importance=0 means completed
        try:
            resp = await self._client.get(f"/memories/{user_id}", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"[MemoryStore] Failed to get goals: {e}")
            return []

    async def get_relationships(self, user_id: str, limit: int = 10) -> list[dict]:
        """Get relationship memories sorted by importance."""
        try:
            resp = await self._client.get(
                f"/memories/{user_id}",
                params={"memory_type": "relationship", "limit": limit, "order_by": "importance"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"[MemoryStore] Failed to get relationships: {e}")
            return []

    async def get_latest_reflection(self, user_id: str) -> dict | None:
        """Get the most recent reflection."""
        try:
            resp = await self._client.get(
                f"/memories/{user_id}",
                params={"memory_type": "reflection", "limit": 1, "order_by": "recent"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data[0] if data else None
        except Exception as e:
            logger.warning(f"[MemoryStore] Failed to get latest reflection: {e}")
            return None

    async def get_relationship_with(self, user_id: str, target_user_id: str) -> dict | None:
        """Get the relationship memory for a specific pair."""
        try:
            resp = await self._client.get(
                f"/memories/{user_id}/relationship/{target_user_id}",
            )
            resp.raise_for_status()
            data = resp.json()
            if "detail" in data:
                return None
            return data
        except Exception:
            return None

    # ---- maintenance ----

    async def prune(self, user_id: str, current_round: int) -> int:
        """Remove expired and excess memories."""
        try:
            resp = await self._client.delete(
                f"/memories/prune/{user_id}",
                params={"current_round": current_round},
            )
            resp.raise_for_status()
            return resp.json().get("deleted", 0)
        except Exception as e:
            logger.warning(f"[MemoryStore] Failed to prune memories: {e}")
            return 0
