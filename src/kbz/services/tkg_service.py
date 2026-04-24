"""TKG service — idempotent CRUD over nodes, edges, embeddings.

All writes assume the caller holds an AsyncSession; the service does NOT
commit — the caller is responsible for transaction boundaries. This lets the
ingestor batch multiple edges per event inside a single transaction.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.models.tkg import TKGEdge, TKGEmbedding, TKGNode

logger = logging.getLogger(__name__)


class TKGService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- nodes ------------------------------------------------
    async def upsert_node(
        self,
        node_id: uuid.UUID,
        kind: str,
        *,
        label: str | None = None,
        community_id: uuid.UUID | None = None,
        round_num: int | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        """Insert-or-update a node. Existing rows get last_seen_round bumped
        and new attrs merged on top of the existing jsonb blob.
        """
        values = {
            "id": node_id,
            "kind": kind,
            "label": label,
            "community_id": community_id,
            "attrs": attrs or {},
            "first_seen_round": round_num,
            "last_seen_round": round_num,
        }
        stmt = pg_insert(TKGNode).values(**values)
        # On conflict: only touch label / last_seen_round / attrs; never rewrite
        # kind or first_seen_round (those are set once).
        update_cols: dict[str, Any] = {}
        if label is not None:
            update_cols["label"] = stmt.excluded.label
        if round_num is not None:
            update_cols["last_seen_round"] = func.greatest(
                TKGNode.last_seen_round, stmt.excluded.last_seen_round
            )
        if attrs:
            # Merge JSONB: existing || new (new wins for overlapping keys).
            update_cols["attrs"] = TKGNode.attrs.op("||")(stmt.excluded.attrs)
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
        await self.db.execute(stmt)

    # ---------- edges ------------------------------------------------
    async def open_edge(
        self,
        src_id: uuid.UUID,
        dst_id: uuid.UUID,
        relation: str,
        *,
        valid_from_round: int,
        community_id: uuid.UUID | None = None,
        weight: float = 1.0,
        attrs: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Open a new edge if no open edge with the same (src,dst,relation)
        exists. If one does, return its id (idempotent) and optionally
        increment weight by 1.0 for repeat events.
        """
        existing = (
            await self.db.execute(
                select(TKGEdge.id, TKGEdge.weight).where(
                    TKGEdge.src_id == src_id,
                    TKGEdge.dst_id == dst_id,
                    TKGEdge.relation == relation,
                    TKGEdge.valid_to_round.is_(None),
                )
            )
        ).first()
        if existing:
            # Bump weight — represents "supported again" / "commented again".
            await self.db.execute(
                update(TKGEdge)
                .where(TKGEdge.id == existing.id)
                .values(weight=TKGEdge.weight + 1.0)
            )
            return existing.id

        new_id = uuid.uuid4()
        self.db.add(
            TKGEdge(
                id=new_id,
                src_id=src_id,
                dst_id=dst_id,
                relation=relation,
                community_id=community_id,
                valid_from_round=valid_from_round,
                weight=weight,
                attrs=attrs or {},
            )
        )
        return new_id

    async def close_edge(
        self,
        src_id: uuid.UUID,
        dst_id: uuid.UUID,
        relation: str,
        *,
        valid_to_round: int,
    ) -> int:
        """Close all open edges matching (src,dst,relation) by setting their
        valid_to_round. Returns number of rows updated.
        """
        result = await self.db.execute(
            update(TKGEdge)
            .where(
                TKGEdge.src_id == src_id,
                TKGEdge.dst_id == dst_id,
                TKGEdge.relation == relation,
                TKGEdge.valid_to_round.is_(None),
            )
            .values(valid_to_round=valid_to_round, ended_at=func.now())
        )
        return result.rowcount or 0

    async def prune_closed_edges(self, older_than_round: int) -> int:
        """Delete edges that were closed before `older_than_round`. Open
        edges are never pruned here — they're the live graph.
        """
        from sqlalchemy import delete as sa_delete

        result = await self.db.execute(
            sa_delete(TKGEdge).where(
                TKGEdge.valid_to_round.is_not(None),
                TKGEdge.valid_to_round < older_than_round,
            )
        )
        return result.rowcount or 0

    # ---------- embeddings -------------------------------------------
    async def add_embedding(
        self,
        node_id: uuid.UUID,
        content: str,
        embedding: list[float],
        round_num: int | None = None,
        *,
        model: str = "nomic-embed-text",
    ) -> None:
        """Upsert an embedding for a node. If an embedding already exists we
        replace it — agents re-summarize over time and the newest wins.
        """
        stmt = pg_insert(TKGEmbedding).values(
            node_id=node_id,
            content=content,
            embedding=embedding,
            model=model,
            round_num=round_num,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["node_id"],
            set_={
                "content": stmt.excluded.content,
                "embedding": stmt.excluded.embedding,
                "model": stmt.excluded.model,
                "round_num": stmt.excluded.round_num,
            },
        )
        await self.db.execute(stmt)
