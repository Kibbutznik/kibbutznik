"""TKG ingestor — subscribes to event_bus and writes nodes/edges in real time.

Runs as a long-lived asyncio task started in the FastAPI lifespan. Mirrors
the pattern used by `_artifact_cascade_loop` in main.py.

Two queues:
  - `_queue` (from event_bus)     — ingests nodes/edges synchronously
  - `_embed_queue` (internal)     — embedding work is offloaded so the
                                    hot ingest path never blocks on Ollama

Event dispatch:
  proposal.created   → upsert(proposal)   + open_edge(AUTHORED)        + queue embed
  proposal.accepted  → upsert(proposal)   + attrs.accepted_at          + queue embed
  proposal.rejected  → upsert(proposal)   + attrs.rejected_at
  support.cast       → open_edge(SUPPORTED, user→proposal)
                       + open_edge(ALLIED_WITH, user→proposal.author)
  support.withdrawn  → close_edge(SUPPORTED)
  pulse.executed     → upsert(pulse)
  comment.posted     → open_edge(COMMENTED_ON) + queue embed
  community.completed → upsert(container) + attrs.committed

Unknown events are ignored silently.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kbz.models.proposal import Proposal
from kbz.models.tkg import TKGNodeKind, TKGRelation
from kbz.services.embedding_service import EmbeddingService
from kbz.services.event_bus import Event, event_bus
from kbz.services.tkg_service import TKGService

logger = logging.getLogger(__name__)


def _to_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


@dataclass
class _EmbedJob:
    node_id: uuid.UUID
    content: str
    round_num: int | None


class TKGIngestor:
    """Long-lived TKG ingestion task. Start/stop tied to FastAPI lifespan."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: EmbeddingService | None = None,
    ):
        self._sf = session_factory
        self._embedder = embedder or EmbeddingService()
        self._queue: asyncio.Queue[Event] | None = None
        self._embed_queue: asyncio.Queue[_EmbedJob] = asyncio.Queue()
        self._ingest_task: asyncio.Task | None = None
        self._embed_task: asyncio.Task | None = None
        self._stopping = False

    # ---------- lifecycle --------------------------------------------
    async def start(self) -> None:
        self._queue = event_bus.subscribe()
        self._ingest_task = asyncio.create_task(self._run(), name="tkg-ingestor")
        self._embed_task = asyncio.create_task(self._embed_loop(), name="tkg-embedder")
        logger.info("[TKGIngestor] started")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._ingest_task, self._embed_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._queue is not None:
            try:
                event_bus.unsubscribe(self._queue)
            except ValueError:
                pass
        await self._embedder.close()
        logger.info("[TKGIngestor] stopped")

    # ---------- ingest loop ------------------------------------------
    async def _run(self) -> None:
        assert self._queue is not None
        try:
            while not self._stopping:
                evt = await self._queue.get()
                try:
                    async with self._sf() as db:
                        await self._handle(db, evt)
                        await db.commit()
                except Exception:
                    logger.exception(
                        "[TKGIngestor] ingest failed for %s (data=%s)",
                        evt.event_type, evt.data,
                    )
        except asyncio.CancelledError:
            raise

    async def _embed_loop(self) -> None:
        try:
            while not self._stopping:
                job = await self._embed_queue.get()
                try:
                    vec = await self._embedder.embed(job.content)
                    if any(v != 0.0 for v in vec):
                        async with self._sf() as db:
                            svc = TKGService(db)
                            await svc.add_embedding(
                                node_id=job.node_id,
                                content=job.content,
                                embedding=vec,
                                round_num=job.round_num,
                                model=self._embedder.model,
                            )
                            await db.commit()
                except Exception:
                    logger.exception(
                        "[TKGIngestor] embed failed for node=%s", job.node_id,
                    )
        except asyncio.CancelledError:
            raise

    # ---------- dispatch ---------------------------------------------
    async def _handle(self, db: AsyncSession, evt: Event) -> None:
        svc = TKGService(db)
        et = evt.event_type
        data = evt.data
        community_id = evt.community_id
        user_id = evt.user_id
        round_num = data.get("round_num")  # optional; most events don't carry it

        if et == "proposal.created":
            proposal_id = _to_uuid(data.get("proposal_id"))
            if not proposal_id or not user_id:
                return
            ptype = data.get("proposal_type") or ""
            ptext = (data.get("proposal_text") or "")[:200]
            # Ensure author node exists
            await svc.upsert_node(
                user_id, TKGNodeKind.USER, community_id=community_id,
                round_num=round_num,
            )
            # Ensure proposal node exists
            await svc.upsert_node(
                proposal_id,
                TKGNodeKind.PROPOSAL,
                label=f"{ptype}: {ptext[:60]}",
                community_id=community_id,
                round_num=round_num,
                attrs={"proposal_type": ptype, "text": ptext},
            )
            await svc.open_edge(
                user_id, proposal_id, TKGRelation.AUTHORED,
                valid_from_round=round_num or 0, community_id=community_id,
            )
            if ptext:
                self._enqueue_embed(proposal_id, ptext, round_num)

        elif et in ("proposal.accepted", "proposal.rejected"):
            proposal_id = _to_uuid(data.get("proposal_id"))
            if not proposal_id:
                return
            status_key = "accepted_at_round" if et.endswith("accepted") else "rejected_at_round"
            await svc.upsert_node(
                proposal_id, TKGNodeKind.PROPOSAL, community_id=community_id,
                round_num=round_num, attrs={status_key: round_num},
            )

        elif et == "support.cast":
            proposal_id = _to_uuid(data.get("proposal_id"))
            author_id = _to_uuid(data.get("author_id"))
            if not (user_id and proposal_id):
                return
            await svc.upsert_node(
                user_id, TKGNodeKind.USER, community_id=community_id,
                round_num=round_num,
            )
            await svc.upsert_node(
                proposal_id, TKGNodeKind.PROPOSAL, community_id=community_id,
                round_num=round_num,
            )
            await svc.open_edge(
                user_id, proposal_id, TKGRelation.SUPPORTED,
                valid_from_round=round_num or 0, community_id=community_id,
            )
            if author_id and author_id != user_id:
                await svc.upsert_node(
                    author_id, TKGNodeKind.USER, community_id=community_id,
                    round_num=round_num,
                )
                await svc.open_edge(
                    user_id, author_id, TKGRelation.ALLIED_WITH,
                    valid_from_round=round_num or 0, community_id=community_id,
                )

        elif et == "support.withdrawn":
            proposal_id = _to_uuid(data.get("proposal_id"))
            if not (user_id and proposal_id):
                return
            await svc.close_edge(
                user_id, proposal_id, TKGRelation.SUPPORTED,
                valid_to_round=round_num or 0,
            )

        elif et == "pulse.executed":
            pulse_id = _to_uuid(data.get("pulse_id"))
            if not pulse_id:
                return
            await svc.upsert_node(
                pulse_id, TKGNodeKind.PULSE, community_id=community_id,
                round_num=round_num,
            )

        elif et == "comment.posted":
            comment_id = _to_uuid(data.get("comment_id"))
            target_id = _to_uuid(data.get("entity_id"))
            target_kind = data.get("entity_type") or "proposal"
            ctext = (data.get("comment_text") or "")[:240]
            if not (user_id and target_id):
                return
            await svc.upsert_node(
                user_id, TKGNodeKind.USER, community_id=community_id,
                round_num=round_num,
            )
            # target kind mapping: "proposal" stays a proposal node; chat on
            # "community" becomes a community node.
            kind = TKGNodeKind.PROPOSAL if target_kind == "proposal" else TKGNodeKind.COMMUNITY
            await svc.upsert_node(
                target_id, kind, community_id=community_id, round_num=round_num,
            )
            edge_id = await svc.open_edge(
                user_id, target_id, TKGRelation.COMMENTED_ON,
                valid_from_round=round_num or 0, community_id=community_id,
                attrs={"comment_id": str(comment_id)} if comment_id else None,
            )
            # Embed the comment text anchored on the comment's synthetic node —
            # but since we don't create a separate comment node, we anchor to
            # the edge's target (the proposal). This is intentionally lossy: a
            # proposal node ends up with the embedding of its *most recent*
            # comment when multiple comments come in, which is fine for "what
            # did people last say about X" semantic queries.
            _ = edge_id
            if ctext:
                self._enqueue_embed(target_id, ctext, round_num)

        elif et == "community.completed":
            container_id = _to_uuid(data.get("container_id"))
            if not container_id:
                return
            await svc.upsert_node(
                container_id, TKGNodeKind.CONTAINER, community_id=community_id,
                round_num=round_num, attrs={"committed": True},
            )

        # Unknown event types are ignored — the ingestor is forward-compatible.

    # ---------- helpers ----------------------------------------------
    def _enqueue_embed(
        self, node_id: uuid.UUID, content: str, round_num: int | None
    ) -> None:
        try:
            self._embed_queue.put_nowait(_EmbedJob(node_id, content, round_num))
        except asyncio.QueueFull:
            logger.warning("[TKGIngestor] embed queue full — dropping job")
