"""Cross-entity search.

GET /search?q=<text>[&kind=community|statement|proposal][&community_id=<scope>]

Closes the "I know I read something about onboarding somewhere"
gap. Today the only navigation off /communities is via known IDs;
there's no way to look across kibbutzim or proposals for a
keyword. This is the cheapest viable surface that solves that:

- Case-insensitive substring match (LOWER(field) LIKE %q%).
- Three entity kinds in one shot: communities (by name +
  charter_md if present), statements (by statement_text),
  proposals (by proposal_text + pitch).
- Optional `community_id` scopes statements/proposals to a
  single kibbutz; communities are always platform-wide.

Future: semantic search via embedding_service / pgvector — the
TKG already has the vectors, this just doesn't use them yet.

We escape LIKE wildcards (`%` `_` `\\`) in the query so a user
typing "100%" doesn't get every row. Empty / whitespace-only
queries 400 — silently returning everything is a worse UX than
"please type something".
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.statement import Statement

router = APIRouter()


class SearchHit(BaseModel):
    kind: Literal["community", "statement", "proposal"]
    id: uuid.UUID
    community_id: uuid.UUID
    title: str  # name for community / statement_text for statement / proposal_text for proposal
    snippet: str | None = None
    created_at: datetime | None = None


_LIKE_ESCAPE_TABLE = str.maketrans({"\\": r"\\", "%": r"\%", "_": r"\_"})


def _esc(q: str) -> str:
    """Escape LIKE wildcards so user-typed '%' doesn't blow up the
    pattern. Backslash itself is escaped first to avoid recursion."""
    return q.translate(_LIKE_ESCAPE_TABLE)


def _snippet(text: str | None, q: str, window: int = 80) -> str | None:
    """Return a short window centered on the first match of q (case-
    insensitive). None if no match — caller decides whether to show
    a fallback. Keeps the response small even when the source is
    long-form (charters, full proposals)."""
    if not text:
        return None
    lo = text.lower()
    idx = lo.find(q.lower())
    if idx < 0:
        return text[: window * 2] + ("…" if len(text) > window * 2 else "")
    start = max(0, idx - window)
    end = min(len(text), idx + len(q) + window)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


@router.get("/search", response_model=list[SearchHit])
async def search(
    q: str = Query(
        ..., min_length=1, max_length=200,
        description="Substring to match. Case-insensitive. LIKE wildcards are escaped.",
    ),
    kind: Literal["community", "statement", "proposal"] | None = Query(
        default=None,
        description="Restrict to one entity kind. Default = all three.",
    ),
    community_id: uuid.UUID | None = Query(
        default=None,
        description="Scope statement/proposal hits to one community.",
    ),
    limit_per_kind: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    qstrip = q.strip()
    if not qstrip:
        raise HTTPException(
            status_code=400,
            detail="Search query cannot be empty or whitespace-only",
        )
    pattern = f"%{_esc(qstrip).lower()}%"
    out: list[SearchHit] = []

    if kind in (None, "community"):
        # `charter_md` ships on a separate (currently unmerged)
        # feature branch, so check at runtime whether the column
        # exists. When it does, search across name + charter; when
        # it doesn't, just name.
        has_charter = hasattr(Community, "charter_md")
        if has_charter:
            community_q = select(Community).where(or_(
                func.lower(Community.name).like(pattern, escape="\\"),
                func.lower(func.coalesce(Community.charter_md, "")).like(
                    pattern, escape="\\",
                ),
            ))
        else:
            community_q = select(Community).where(
                func.lower(Community.name).like(pattern, escape="\\"),
            )
        rows = (
            await db.execute(
                community_q
                .order_by(Community.created_at.desc())
                .limit(limit_per_kind)
            )
        ).scalars().all()
        for c in rows:
            charter = getattr(c, "charter_md", None)
            out.append(SearchHit(
                kind="community",
                id=c.id,
                community_id=c.id,
                title=c.name,
                snippet=_snippet(charter, qstrip),
                created_at=c.created_at,
            ))

    if kind in (None, "statement"):
        stmt = select(Statement).where(
            func.lower(Statement.statement_text).like(pattern, escape="\\"),
        )
        if community_id is not None:
            stmt = stmt.where(Statement.community_id == community_id)
        stmt = stmt.order_by(Statement.created_at.desc()).limit(limit_per_kind)
        rows = (await db.execute(stmt)).scalars().all()
        for s in rows:
            out.append(SearchHit(
                kind="statement",
                id=s.id,
                community_id=s.community_id,
                title=s.statement_text[:140],
                snippet=_snippet(s.statement_text, qstrip),
                created_at=s.created_at,
            ))

    if kind in (None, "proposal"):
        stmt = select(Proposal).where(
            or_(
                func.lower(Proposal.proposal_text).like(pattern, escape="\\"),
                func.lower(func.coalesce(Proposal.pitch, "")).like(
                    pattern, escape="\\",
                ),
            )
        )
        if community_id is not None:
            stmt = stmt.where(Proposal.community_id == community_id)
        stmt = stmt.order_by(Proposal.created_at.desc()).limit(limit_per_kind)
        rows = (await db.execute(stmt)).scalars().all()
        for p in rows:
            # Prefer matching the query against pitch text if it's
            # there — proposals are dense and the pitch is usually
            # the more readable hit context.
            snippet_src = (
                p.pitch
                if (p.pitch and qstrip.lower() in (p.pitch or "").lower())
                else p.proposal_text
            )
            out.append(SearchHit(
                kind="proposal",
                id=p.id,
                community_id=p.community_id,
                title=(p.proposal_text or "")[:140],
                snippet=_snippet(snippet_src, qstrip),
                created_at=p.created_at,
            ))

    return out
