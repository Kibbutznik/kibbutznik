"""Pydantic DTOs for the /tkg router."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class NeighborOut(BaseModel):
    edge_id: uuid.UUID
    src_id: uuid.UUID
    dst_id: uuid.UUID
    relation: str
    weight: float
    valid_from_round: int
    valid_to_round: int | None
    attrs: dict[str, Any] = Field(default_factory=dict)
    # Enriched from tkg_nodes (the "other end") when available
    neighbor_kind: str | None = None
    neighbor_label: str | None = None


class EdgeOut(BaseModel):
    edge_id: uuid.UUID
    src_id: uuid.UUID
    dst_id: uuid.UUID
    relation: str
    weight: float
    valid_from_round: int
    valid_to_round: int | None
    attrs: dict[str, Any] = Field(default_factory=dict)


class SemanticSearchIn(BaseModel):
    user_id: uuid.UUID | None = None
    query: str
    limit: int = 10
    community_id: uuid.UUID | None = None
    from_round: int | None = None
    to_round: int | None = None
    kind: str | None = None   # optional filter: only nodes of this kind


class SemanticHit(BaseModel):
    node_id: uuid.UUID
    kind: str
    label: str | None
    content: str
    score: float
    round_num: int | None
