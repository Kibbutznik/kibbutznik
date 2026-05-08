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
from kbz.models.artifact import Artifact
from kbz.models.artifact_container import ArtifactContainer
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.statement import Statement
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

    # ── Bulk-resolve UUIDs in val_uuid / val_text into human names ──
    # Pre-fix the highlight cards rendered raw 36-char UUIDs whenever
    # a proposal type put an id in val_uuid or val_text (most painful:
    # DelegateArtifact, whose val_text IS a UUID — the user reported
    # a card titled "be866e6e-9918-49a0-..."). For each artifact /
    # container / community / statement id referenced by any proposal
    # in this batch, look up the readable name in one query, then
    # build snippets from those names instead of the raw ids.
    artifact_ids: set[uuid.UUID] = set()
    container_ids: set[uuid.UUID] = set()
    community_ids: set[uuid.UUID] = set()
    statement_ids: set[uuid.UUID] = set()
    for proposal, _, _ in rows:
        ptype = proposal.proposal_type
        if proposal.val_uuid:
            if ptype in ("EditArtifact", "DelegateArtifact", "RemoveArtifact"):
                artifact_ids.add(proposal.val_uuid)
            elif ptype in ("CreateArtifact", "CommitArtifact"):
                container_ids.add(proposal.val_uuid)
            elif ptype in ("RemoveStatement", "ReplaceStatement"):
                statement_ids.add(proposal.val_uuid)
        if proposal.val_text and ptype == "DelegateArtifact":
            try:
                community_ids.add(uuid.UUID(proposal.val_text.strip()))
            except (ValueError, AttributeError):
                pass

    artifact_titles: dict[uuid.UUID, str] = {}
    if artifact_ids:
        for aid, title in (
            await db.execute(
                select(Artifact.id, Artifact.title).where(Artifact.id.in_(artifact_ids))
            )
        ).all():
            artifact_titles[aid] = (title or "").strip() or "Untitled"

    container_titles: dict[uuid.UUID, str] = {}
    if container_ids:
        for cid_, title in (
            await db.execute(
                select(ArtifactContainer.id, ArtifactContainer.title).where(
                    ArtifactContainer.id.in_(container_ids)
                )
            )
        ).all():
            container_titles[cid_] = (title or "").strip() or "Untitled container"

    target_community_names: dict[uuid.UUID, str] = {}
    if community_ids:
        for cid_, name in (
            await db.execute(
                select(Community.id, Community.name).where(Community.id.in_(community_ids))
            )
        ).all():
            target_community_names[cid_] = (name or "").strip() or "Unnamed action"

    statement_texts: dict[uuid.UUID, str] = {}
    if statement_ids:
        for sid, text_ in (
            await db.execute(
                select(Statement.id, Statement.statement_text).where(
                    Statement.id.in_(statement_ids)
                )
            )
        ).all():
            statement_texts[sid] = (text_ or "").strip() or "Statement"

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

        # Build the human-friendly summary using the resolved names.
        ptype = proposal.proposal_type
        text = (proposal.proposal_text or "").strip()
        val_text = (proposal.val_text or "").strip()
        link_url = None
        if ptype in ("EditArtifact", "DelegateArtifact", "RemoveArtifact") and proposal.val_uuid:
            link_url = f"/artifact/{proposal.val_uuid}"
        else:
            link_url = "/kbz/viewer/"

        # Per-type rendering.
        if ptype == "DelegateArtifact" and proposal.val_uuid:
            art = artifact_titles.get(proposal.val_uuid, "an artifact")
            try:
                target_id = uuid.UUID(val_text) if val_text else None
            except ValueError:
                target_id = None
            target = target_community_names.get(target_id, "a working group") if target_id else "a working group"
            snippet = f'"{art}" → {target}'
        elif ptype == "EditArtifact" and proposal.val_uuid:
            art = artifact_titles.get(proposal.val_uuid, "an artifact")
            snippet = f'"{art}"'
        elif ptype == "RemoveArtifact" and proposal.val_uuid:
            art = artifact_titles.get(proposal.val_uuid, "an artifact")
            snippet = f'Retired: "{art}"'
        elif ptype == "CreateArtifact":
            # val_text holds the new artifact's title (the slot name)
            snippet = val_text or "(new artifact)"
        elif ptype == "CommitArtifact" and proposal.val_uuid:
            cont = container_titles.get(proposal.val_uuid, "a container")
            snippet = f'Shipped: "{cont}"'
        elif ptype == "ChangeVariable":
            # proposal_text starts with "VarName\nreason..."; val_text is new value
            var_name = text.split("\n", 1)[0].strip() if text else "(variable)"
            new_val = val_text if val_text else "(value)"
            snippet = f'{var_name} → {new_val}'
        elif ptype == "RemoveStatement" and proposal.val_uuid:
            stmt = statement_texts.get(proposal.val_uuid, "a rule")[:80]
            snippet = f'Retired: "{stmt}"'
        elif ptype == "ReplaceStatement":
            new_stmt = (val_text or "")[:80]
            old_stmt = statement_texts.get(proposal.val_uuid or uuid.uuid4(), "")[:60]
            if new_stmt and old_stmt:
                snippet = f'"{old_stmt}…" → "{new_stmt}…"'
            elif new_stmt:
                snippet = f'New wording: "{new_stmt}"'
            else:
                snippet = "Rule rewritten"
        elif ptype == "AddStatement":
            snippet = text or "(new rule)"
        elif ptype == "AddAction":
            snippet = val_text or text or "(new working group)"
        elif ptype in ("Funding", "Payment", "Dividend"):
            snippet = f"{ptype}: {val_text}" if val_text else ptype
        else:
            snippet = val_text or text or f"({ptype})"

        # Trim — keep it tight. First line only, max 120 chars.
        snippet = snippet.split("\n", 1)[0][:120].strip()

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
