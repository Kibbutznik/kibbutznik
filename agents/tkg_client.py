"""Agent-side HTTP client for the Temporal Knowledge Graph API.

Mirrors agents/memory.py:MemoryStore so callers can mix the two.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TKGClient:
    """Typed async client for the /tkg/* endpoints."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 15.0):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def neighbors(
        self,
        entity_id: str,
        *,
        at_round: int | None = None,
        relation: str | None = None,
        depth: int = 1,
        limit: int = 50,
    ) -> list[dict]:
        params: dict[str, Any] = {"depth": depth, "limit": limit}
        if at_round is not None:
            params["at_round"] = at_round
        if relation:
            params["relation"] = relation
        try:
            resp = await self._client.get(f"/tkg/neighbors/{entity_id}", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("[TKGClient] neighbors failed: %s", e)
            return []

    async def timeline(
        self,
        entity_id: str,
        *,
        from_round: int = 0,
        to_round: int | None = None,
        relations: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict]:
        params: dict[str, Any] = {"from_round": from_round, "limit": limit}
        if to_round is not None:
            params["to_round"] = to_round
        if relations:
            params["relations"] = relations
        try:
            resp = await self._client.get(f"/tkg/timeline/{entity_id}", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("[TKGClient] timeline failed: %s", e)
            return []

    async def semantic_search(
        self,
        query: str,
        *,
        user_id: str | None = None,
        community_id: str | None = None,
        from_round: int | None = None,
        to_round: int | None = None,
        kind: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        payload: dict[str, Any] = {"query": query, "limit": limit}
        if user_id:
            payload["user_id"] = user_id
        if community_id:
            payload["community_id"] = community_id
        if from_round is not None:
            payload["from_round"] = from_round
        if to_round is not None:
            payload["to_round"] = to_round
        if kind:
            payload["kind"] = kind
        try:
            resp = await self._client.post("/tkg/semantic_search", json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("[TKGClient] semantic_search failed: %s", e)
            return []

    async def prune_closed_edges(self, older_than_round: int) -> int:
        try:
            resp = await self._client.delete(
                "/tkg/prune", params={"older_than_round": older_than_round}
            )
            resp.raise_for_status()
            return resp.json().get("deleted", 0)
        except Exception as e:
            logger.warning("[TKGClient] prune failed: %s", e)
            return 0
