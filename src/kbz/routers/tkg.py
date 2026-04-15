"""/tkg router — neighbor / timeline / semantic-search endpoints.

These are read-only: writes happen via the TKGIngestor subscribing to the
in-memory event_bus. The router stays thin; all logic lives in SQL.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.schemas.tkg import EdgeOut, NeighborOut, SemanticHit, SemanticSearchIn
from kbz.services.embedding_service import EmbeddingService

router = APIRouter(prefix="/tkg", tags=["tkg"])


# Shared singleton — the FastAPI app lives one process and a single
# EmbeddingService is fine (httpx AsyncClient is itself thread-safe across
# concurrent awaits). Created lazily on first request.
_embedder: EmbeddingService | None = None


def _get_embedder() -> EmbeddingService:
    global _embedder
    if _embedder is None:
        _embedder = EmbeddingService()
    return _embedder


def _row_to_neighbor(row: Any) -> NeighborOut:
    return NeighborOut(
        edge_id=row.edge_id,
        src_id=row.src_id,
        dst_id=row.dst_id,
        relation=row.relation,
        weight=row.weight,
        valid_from_round=row.valid_from_round,
        valid_to_round=row.valid_to_round,
        attrs=row.attrs or {},
        neighbor_kind=getattr(row, "neighbor_kind", None),
        neighbor_label=getattr(row, "neighbor_label", None),
    )


@router.get("/neighbors/{entity_id}", response_model=list[NeighborOut])
async def neighbors(
    entity_id: uuid.UUID,
    at_round: int | None = Query(None, description="Only edges valid at this round"),
    relation: str | None = Query(None, description="Filter by relation type"),
    depth: int = Query(1, ge=1, le=2, description="Hop depth (1 or 2)"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[NeighborOut]:
    """Return outgoing edges from `entity_id`.

    depth=1: single indexed scan on tkg_edges.
    depth=2: recursive CTE capped at two hops (DB-side safety).

    If `at_round` is set, only edges whose validity interval covers that
    round are returned — for "what did I look like at round N" queries.
    """
    params: dict[str, Any] = {"entity_id": entity_id, "limit": limit}
    filters = ["e.src_id = :entity_id"]
    if relation is not None:
        filters.append("e.relation = :relation")
        params["relation"] = relation
    if at_round is not None:
        filters.append(
            "e.valid_from_round <= :at_round "
            "AND (e.valid_to_round IS NULL OR e.valid_to_round > :at_round)"
        )
        params["at_round"] = at_round

    where = " AND ".join(filters)

    if depth == 1:
        sql = text(
            f"""
            SELECT
                e.id                AS edge_id,
                e.src_id            AS src_id,
                e.dst_id            AS dst_id,
                e.relation          AS relation,
                e.weight            AS weight,
                e.valid_from_round  AS valid_from_round,
                e.valid_to_round    AS valid_to_round,
                e.attrs             AS attrs,
                n.kind              AS neighbor_kind,
                n.label             AS neighbor_label
            FROM tkg_edges e
            LEFT JOIN tkg_nodes n ON n.id = e.dst_id
            WHERE {where}
            ORDER BY e.weight DESC, e.valid_from_round DESC
            LIMIT :limit
            """
        )
        rows = (await db.execute(sql, params)).all()
        return [_row_to_neighbor(r) for r in rows]

    # depth == 2: two-hop CTE. Safety cap via LIMIT on each hop.
    sql = text(
        f"""
        WITH hop1 AS (
            SELECT e.*
            FROM tkg_edges e
            WHERE {where}
            LIMIT :limit
        ),
        hop2 AS (
            SELECT e2.*
            FROM tkg_edges e2
            JOIN hop1 h ON h.dst_id = e2.src_id
            WHERE (:relation::text IS NULL OR e2.relation = :relation)
              AND (
                :at_round::int IS NULL
                OR (
                    e2.valid_from_round <= :at_round
                    AND (e2.valid_to_round IS NULL OR e2.valid_to_round > :at_round)
                )
              )
            LIMIT :limit
        ),
        combined AS (
            SELECT * FROM hop1
            UNION ALL
            SELECT * FROM hop2
        )
        SELECT
            c.id                AS edge_id,
            c.src_id            AS src_id,
            c.dst_id            AS dst_id,
            c.relation          AS relation,
            c.weight            AS weight,
            c.valid_from_round  AS valid_from_round,
            c.valid_to_round    AS valid_to_round,
            c.attrs             AS attrs,
            n.kind              AS neighbor_kind,
            n.label             AS neighbor_label
        FROM combined c
        LEFT JOIN tkg_nodes n ON n.id = c.dst_id
        ORDER BY c.weight DESC, c.valid_from_round DESC
        LIMIT :limit
        """
    )
    # Ensure keys exist even when not filtering on them.
    params.setdefault("relation", None)
    params.setdefault("at_round", None)
    rows = (await db.execute(sql, params)).all()
    return [_row_to_neighbor(r) for r in rows]


@router.get("/timeline/{entity_id}", response_model=list[EdgeOut])
async def timeline(
    entity_id: uuid.UUID,
    from_round: int = Query(0, ge=0),
    to_round: int | None = Query(None),
    relations: list[str] | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> list[EdgeOut]:
    """Return all edges touching `entity_id` (either direction) whose validity
    intersects the [from_round, to_round] window, ordered chronologically.
    """
    params: dict[str, Any] = {
        "entity_id": entity_id,
        "from_round": from_round,
        "limit": limit,
    }
    filters = [
        "(e.src_id = :entity_id OR e.dst_id = :entity_id)",
        # interval intersection: edge starts before window ends AND ends after window starts
        "e.valid_from_round >= :from_round",
    ]
    if to_round is not None:
        filters.append("e.valid_from_round <= :to_round")
        params["to_round"] = to_round
    if relations:
        filters.append("e.relation = ANY(:relations)")
        params["relations"] = list(relations)

    where = " AND ".join(filters)
    sql = text(
        f"""
        SELECT
            e.id               AS edge_id,
            e.src_id           AS src_id,
            e.dst_id           AS dst_id,
            e.relation         AS relation,
            e.weight           AS weight,
            e.valid_from_round AS valid_from_round,
            e.valid_to_round   AS valid_to_round,
            e.attrs            AS attrs
        FROM tkg_edges e
        WHERE {where}
        ORDER BY e.valid_from_round ASC, e.created_at ASC
        LIMIT :limit
        """
    )
    rows = (await db.execute(sql, params)).all()
    return [
        EdgeOut(
            edge_id=r.edge_id,
            src_id=r.src_id,
            dst_id=r.dst_id,
            relation=r.relation,
            weight=r.weight,
            valid_from_round=r.valid_from_round,
            valid_to_round=r.valid_to_round,
            attrs=r.attrs or {},
        )
        for r in rows
    ]


@router.post("/semantic_search", response_model=list[SemanticHit])
async def semantic_search(
    body: SemanticSearchIn,
    db: AsyncSession = Depends(get_db),
) -> list[SemanticHit]:
    """Embed `body.query` via Ollama, then run a pgvector cosine KNN against
    tkg_embeddings, optionally filtered by community_id / round window / kind.
    """
    if not body.query or not body.query.strip():
        raise HTTPException(status_code=400, detail="query must be non-empty")

    embedder = _get_embedder()
    vec = await embedder.embed(body.query)
    if not any(v != 0.0 for v in vec):
        # embedding failed — don't return random results
        return []

    params: dict[str, Any] = {
        "q": vec,
        "limit": body.limit,
    }
    filters = []
    if body.community_id is not None:
        filters.append("n.community_id = :community_id")
        params["community_id"] = body.community_id
    if body.from_round is not None:
        filters.append("em.round_num >= :from_round")
        params["from_round"] = body.from_round
    if body.to_round is not None:
        filters.append("em.round_num <= :to_round")
        params["to_round"] = body.to_round
    if body.kind:
        filters.append("n.kind = :kind")
        params["kind"] = body.kind

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    # Note: `embedding <=> :q` uses cosine distance (0=identical, 2=opposite).
    # score = 1 - distance gives cosine similarity in [-1, 1].
    sql = text(
        f"""
        SELECT
            em.node_id                      AS node_id,
            n.kind                          AS kind,
            n.label                         AS label,
            em.content                      AS content,
            1 - (em.embedding <=> CAST(:q AS vector)) AS score,
            em.round_num                    AS round_num
        FROM tkg_embeddings em
        JOIN tkg_nodes n ON n.id = em.node_id
        {where}
        ORDER BY em.embedding <=> CAST(:q AS vector)
        LIMIT :limit
        """
    )
    rows = (await db.execute(sql, params)).all()
    return [
        SemanticHit(
            node_id=r.node_id,
            kind=r.kind,
            label=r.label,
            content=r.content,
            score=float(r.score),
            round_num=r.round_num,
        )
        for r in rows
    ]


@router.delete("/prune")
async def prune(
    older_than_round: int = Query(..., ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Delete closed edges whose valid_to_round < `older_than_round`."""
    from kbz.services.tkg_service import TKGService

    svc = TKGService(db)
    n = await svc.prune_closed_edges(older_than_round)
    await db.commit()
    return {"deleted": n}
