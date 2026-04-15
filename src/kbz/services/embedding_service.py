"""Thin async Ollama embedding client.

Uses the /api/embeddings HTTP endpoint with `nomic-embed-text` by default.
Falls back to a zero-vector on error so a flaky Ollama never propagates into
the agent turn — the caller can filter zero vectors if it wants.
"""

from __future__ import annotations

import logging

import httpx

from kbz.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        timeout: float = 10.0,
    ):
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or settings.ollama_embed_model
        self._dim = dim or settings.tkg_embed_dim
        self._client = httpx.AsyncClient(timeout=timeout)

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model(self) -> str:
        return self._model

    async def close(self) -> None:
        await self._client.aclose()

    async def embed(self, text: str) -> list[float]:
        if not text:
            return [0.0] * self._dim
        try:
            resp = await self._client.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model, "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data.get("embedding") or []
            if len(vec) != self._dim:
                logger.warning(
                    "[EmbeddingService] Unexpected dim %d (expected %d) for model %s",
                    len(vec), self._dim, self._model,
                )
                if not vec:
                    return [0.0] * self._dim
            return vec
        except Exception as e:
            logger.warning("[EmbeddingService] embed failed: %s", e)
            return [0.0] * self._dim

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """No native batch API — sequential calls. Good enough for our
        volume; we can parallelize if profiling shows it's a bottleneck.
        """
        return [await self.embed(t) for t in texts]
