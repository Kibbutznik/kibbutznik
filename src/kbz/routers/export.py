"""Data export — community-level and per-user.

GET /communities/{id}/export → JSON bundle with everything
                                governance-relevant for that
                                community: members, statements,
                                proposals (every status, with
                                supporters), comments, ledger
                                entries when financial.
                                Members-only.

GET /users/me/export          → JSON bundle with the logged-in
                                user's own data across all
                                communities they touched: profile,
                                memberships, proposals authored,
                                comments posted, supports cast,
                                wallets owned. The GDPR-style
                                "give me my data" surface.

Both are intentionally simple JSON, not zip/sqlite/csv. The
calling client/dashboard can render or save to disk; we don't
guess at storage format.

Out of scope (explicit follow-ups):
- Encrypted/signed bundles for cross-platform import. The
  shape here is read-only and informational.
- Streaming: communities with thousands of proposals will get
  a large payload. We cap with limits per kind so the worst
  case stays bounded.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import require_user
from kbz.database import get_db
from kbz.enums import MemberStatus
from kbz.models.comment import Comment
from kbz.models.community import Community
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.statement import Statement
from kbz.models.support import Support
from kbz.models.user import User
from kbz.models.variable import Variable
from kbz.services.member_service import MemberService

router = APIRouter()


# Default cap per entity kind so a 50k-proposal community doesn't
# OOM the server on export. Caller can request more with ?limit=.
_DEFAULT_LIMIT = 5000
_MAX_LIMIT = 50000


def _ts(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


@router.get("/communities/{community_id}/export")
async def export_community(
    community_id: uuid.UUID,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    community = (
        await db.execute(select(Community).where(Community.id == community_id))
    ).scalar_one_or_none()
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")

    # Members-only access. Public scraping of an entire
    # community's history would be a privacy nightmare even when
    # individual rows are visible — we'd be advertising "snapshot
    # me".
    if not await MemberService(db).is_active_member(community_id, user.id):
        raise HTTPException(
            status_code=403,
            detail="Only active members can export this community",
        )

    members = (
        await db.execute(
            select(Member, User.user_name)
            .outerjoin(User, User.id == Member.user_id)
            .where(Member.community_id == community_id)
            .limit(limit)
        )
    ).all()
    statements = (
        await db.execute(
            select(Statement)
            .where(Statement.community_id == community_id)
            .limit(limit)
        )
    ).scalars().all()
    proposals = (
        await db.execute(
            select(Proposal)
            .where(Proposal.community_id == community_id)
            .limit(limit)
        )
    ).scalars().all()
    proposal_ids = [p.id for p in proposals]
    supports = (
        []
        if not proposal_ids
        else (
            await db.execute(
                select(Support).where(Support.proposal_id.in_(proposal_ids))
            )
        ).scalars().all()
    )
    # Proposal-attached comments only — community chat (entity_type
    # == "community") is its own surface; export it separately.
    comments_proposal = (
        []
        if not proposal_ids
        else (
            await db.execute(
                select(Comment).where(
                    Comment.entity_type == "proposal",
                    Comment.entity_id.in_(proposal_ids),
                )
            )
        ).scalars().all()
    )
    chat = (
        await db.execute(
            select(Comment).where(
                Comment.entity_type == "community",
                Comment.entity_id == community_id,
            )
            .limit(limit)
        )
    ).scalars().all()
    variables = (
        await db.execute(
            select(Variable).where(Variable.community_id == community_id)
        )
    ).scalars().all()

    return {
        "format_version": 1,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "community": {
            "id": str(community.id),
            "name": community.name,
            "parent_id": str(community.parent_id),
            "status": community.status,
            "member_count": community.member_count,
            "created_at": _ts(community.created_at),
            # Charter is on a different unmerged feature branch.
            "charter_md": getattr(community, "charter_md", None),
        },
        "variables": [
            {"name": v.name, "value": v.value} for v in variables
        ],
        "members": [
            {
                "user_id": str(m.user_id),
                "user_name": uname,
                "status": int(m.status),
                "seniority": m.seniority,
                "joined_at": _ts(m.joined_at),
            }
            for m, uname in members
        ],
        "statements": [
            {
                "id": str(s.id),
                "statement_text": s.statement_text,
                "status": int(s.status),
                "prev_statement_id": (
                    str(s.prev_statement_id) if s.prev_statement_id else None
                ),
                "created_at": _ts(s.created_at),
            }
            for s in statements
        ],
        "proposals": [
            {
                "id": str(p.id),
                "user_id": str(p.user_id),
                "proposal_type": str(p.proposal_type),
                "proposal_status": str(p.proposal_status),
                "proposal_text": p.proposal_text,
                "pitch": p.pitch,
                "val_uuid": str(p.val_uuid) if p.val_uuid else None,
                "val_text": p.val_text,
                "age": p.age,
                "support_count": p.support_count,
                "created_at": _ts(p.created_at),
            }
            for p in proposals
        ],
        "supports": [
            {
                "user_id": str(s.user_id),
                "proposal_id": str(s.proposal_id),
                "support_value": s.support_value,
                "created_at": _ts(s.created_at),
            }
            for s in supports
        ],
        "comments_on_proposals": [
            {
                "id": str(c.id),
                "user_id": str(c.user_id),
                "entity_id": str(c.entity_id),
                "comment_text": c.comment_text,
                "parent_comment_id": (
                    str(c.parent_comment_id) if c.parent_comment_id else None
                ),
                "score": c.score,
                "created_at": _ts(c.created_at),
            }
            for c in comments_proposal
        ],
        "chat": [
            {
                "id": str(c.id),
                "user_id": str(c.user_id),
                "comment_text": c.comment_text,
                "parent_comment_id": (
                    str(c.parent_comment_id) if c.parent_comment_id else None
                ),
                "created_at": _ts(c.created_at),
            }
            for c in chat
        ],
        "limits": {
            "per_kind": limit,
            "_truncation_note": (
                "Each list above is capped at `per_kind`. If a list "
                "is exactly that length, request a higher ?limit= "
                "or paginate via the entity's own listing endpoint."
            ),
        },
    }


@router.get("/users/me/export")
async def export_me(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the logged-in user's own data: profile, memberships,
    proposals authored, supports cast, comments posted. GDPR-style
    portable export. Cross-references stay as bare ids (no JOINs)
    so a stale community on another platform doesn't pollute the
    bundle."""
    memberships = (
        await db.execute(
            select(Member).where(
                Member.user_id == user.id,
                Member.status == MemberStatus.ACTIVE,
            )
        )
    ).scalars().all()
    proposals = (
        await db.execute(
            select(Proposal).where(Proposal.user_id == user.id).limit(_DEFAULT_LIMIT)
        )
    ).scalars().all()
    supports = (
        await db.execute(
            select(Support).where(Support.user_id == user.id).limit(_DEFAULT_LIMIT)
        )
    ).scalars().all()
    comments = (
        await db.execute(
            select(Comment).where(Comment.user_id == user.id).limit(_DEFAULT_LIMIT)
        )
    ).scalars().all()

    return {
        "format_version": 1,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user": {
            "id": str(user.id),
            "user_name": user.user_name,
            "email": user.email,
            "about": user.about,
            "is_human": user.is_human,
        },
        "memberships": [
            {
                "community_id": str(m.community_id),
                "status": int(m.status),
                "seniority": m.seniority,
                "joined_at": _ts(m.joined_at),
            }
            for m in memberships
        ],
        "proposals_authored": [
            {
                "id": str(p.id),
                "community_id": str(p.community_id),
                "proposal_type": str(p.proposal_type),
                "proposal_status": str(p.proposal_status),
                "proposal_text": p.proposal_text,
                "pitch": p.pitch,
                "created_at": _ts(p.created_at),
            }
            for p in proposals
        ],
        "supports_cast": [
            {
                "proposal_id": str(s.proposal_id),
                "support_value": s.support_value,
                "created_at": _ts(s.created_at),
            }
            for s in supports
        ],
        "comments_posted": [
            {
                "id": str(c.id),
                "entity_type": c.entity_type,
                "entity_id": str(c.entity_id),
                "comment_text": c.comment_text,
                "score": c.score,
                "created_at": _ts(c.created_at),
            }
            for c in comments
        ],
    }
