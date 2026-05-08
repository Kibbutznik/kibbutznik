"""Public "highlight reel" endpoint — surfaces recent meaningful
decisions across all PUBLIC communities so the welcome page (and
any future external embed) can show the project actually moving.

Visibility-gated: private and unlisted communities are excluded.
The fallback for legacy communities (no Visibility row) is "public",
matching CommunityService.get_effective_visibility — so existing
sims keep showing here without any retroactive variable seeding.

Expensive enough that we cache for 30 seconds; the homepage refresh
cycle is way slower than that, and a public read endpoint without
caching is a free DoS vector.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.enums import ProposalStatus
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.user import User
from kbz.models.variable import Variable

router = APIRouter(prefix="", tags=["highlights"])


_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_CACHE_TTL_S = 30.0

# Proposal types that are "interesting" for the public highlight
# reel. Membership / JoinAction / EndAction are routine plumbing and
# not particularly evocative for outsiders; skip them.
_INTERESTING_TYPES = {
    "AddStatement",
    "ReplaceStatement",
    "RemoveStatement",
    "ChangeVariable",
    "AddAction",
    "CreateArtifact",
    "EditArtifact",
    "CommitArtifact",
    "DelegateArtifact",
    "Funding",
    "Payment",
    "Dividend",
}

ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def _public_root_ids(db: AsyncSession) -> set[uuid.UUID]:
    """All root community ids whose effective visibility is public.
    Roots without a Visibility row default to public."""
    roots = (
        await db.execute(
            select(Community.id).where(Community.parent_id == ZERO_UUID)
        )
    ).scalars().all()
    if not roots:
        return set()
    vis_rows = (
        await db.execute(
            select(Variable.community_id, Variable.value).where(
                Variable.community_id.in_(roots),
                Variable.name == "Visibility",
            )
        )
    ).all()
    vis_map = {cid: (val or "").strip().lower() for cid, val in vis_rows}
    return {
        cid for cid in roots
        if vis_map.get(cid, "public") == "public"
    }


async def _root_id_of(db: AsyncSession, community_id: uuid.UUID) -> uuid.UUID:
    """Walk parent_id chain to root. Cycle-safe (cap 64 hops)."""
    cid = community_id
    for _ in range(64):
        row = (
            await db.execute(
                select(Community.parent_id).where(Community.id == cid)
            )
        ).scalar_one_or_none()
        if row is None:
            return cid  # missing community — best-effort, treat as root
        if row == ZERO_UUID:
            return cid
        cid = row
    return cid


@router.get("/highlights")
async def get_highlights(
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Recent accepted proposals across all PUBLIC root communities,
    newest-first. Each item carries enough info to render a card on
    the welcome page or anywhere else.

    Cached for 30s — the calling page (welcome.html) doesn't need
    sub-second freshness, and this endpoint is unauthenticated.
    """
    now = time.time()
    if _CACHE["payload"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL_S:
        cached = _CACHE["payload"]
        # Honor a smaller `limit` than the cached response by slicing.
        if limit < len(cached.get("highlights", [])):
            return {**cached, "highlights": cached["highlights"][:limit]}
        return cached

    public_roots = await _public_root_ids(db)
    if not public_roots:
        payload = {"highlights": [], "total_public_roots": 0}
        _CACHE["ts"] = now
        _CACHE["payload"] = payload
        return payload

    # Pull ~3x the requested number from DB; we'll filter the
    # uninteresting types client-side and trim to `limit`.
    raw_limit = limit * 3
    rows = (
        await db.execute(
            select(Proposal, Community.name, User.user_name)
            .join(Community, Community.id == Proposal.community_id)
            .outerjoin(User, User.id == Proposal.user_id)
            .where(
                Proposal.proposal_status == ProposalStatus.ACCEPTED,
                Proposal.proposal_type.in_(_INTERESTING_TYPES),
            )
            .order_by(Proposal.decided_at.desc().nulls_last())
            .limit(raw_limit * 2)
        )
    ).all()

    out = []
    seen_artifacts: set[uuid.UUID] = set()  # de-dupe consecutive edits to same artifact
    for proposal, comm_name, author_name in rows:
        # Visibility filter: walk to root (or use the proposal's
        # community if it's already a root).
        root_id = await _root_id_of(db, proposal.community_id)
        if root_id not in public_roots:
            continue

        # Suppress duplicate "edited the Plan" stream — keep only the
        # most recent edit per artifact.
        if proposal.proposal_type in ("EditArtifact",) and proposal.val_uuid:
            if proposal.val_uuid in seen_artifacts:
                continue
            seen_artifacts.add(proposal.val_uuid)

        # Build the human-friendly summary.
        ptype = proposal.proposal_type
        text = (proposal.proposal_text or "").strip()
        val_text = (proposal.val_text or "").strip()
        link_url = None
        if ptype in ("EditArtifact", "DelegateArtifact", "RemoveArtifact") and proposal.val_uuid:
            link_url = f"/artifact/{proposal.val_uuid}"
        elif ptype == "CreateArtifact":
            # CreateArtifact doesn't have an artifact id yet; link to
            # the live community view.
            link_url = "/kbz/viewer/"
        else:
            link_url = "/kbz/viewer/"

        snippet = val_text or text
        snippet = snippet.split("\n", 1)[0][:120]

        out.append({
            "id": str(proposal.id),
            "type": ptype,
            "community_id": str(proposal.community_id),
            "community_name": comm_name or "Unknown community",
            "author_name": author_name,
            "title": snippet or f"({ptype})",
            "decided_at": proposal.decided_at.isoformat() if proposal.decided_at else None,
            "link": link_url,
        })

        if len(out) >= limit:
            break

    payload = {"highlights": out, "total_public_roots": len(public_roots)}
    _CACHE["ts"] = now
    _CACHE["payload"] = payload
    return payload
