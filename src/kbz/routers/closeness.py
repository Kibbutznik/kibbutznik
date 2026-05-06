import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import get_current_user, is_observer
from kbz.database import get_db
from kbz.models.user import User
from kbz.services.closeness_service import ClosenessService
from kbz.services.community_service import CommunityService
from kbz.services.member_service import MemberService

router = APIRouter()


@router.get("/communities/{community_id}/closeness")
async def get_community_closeness(
    community_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    """Return the social-graph (members + closeness pairs) for the
    community.

    Pre-fix this was anonymous. The graph includes negative-flag
    deltas — a stranger could pull the full who-likes-whom matrix
    (and who's been flagged-down by whom) for any community by id.
    Now: human callers must be active members; agents (no cookie)
    pass through to keep simulation tooling working. Big Brother
    (the simulation observer the viewer talks through) is also let
    through — it's the operator's own viewer hitting its own sim,
    and otherwise the Relationships tab 403s and shows "no
    relationships yet" against a community that has plenty.
    """
    if await CommunityService(db).get(community_id) is None:
        raise HTTPException(status_code=404, detail="Community not found")
    member_svc = MemberService(db)
    if session_user is not None and not is_observer(session_user):
        if not await member_svc.is_active_member(community_id, session_user.id):
            raise HTTPException(
                status_code=403,
                detail="Only active members can view this community's closeness graph",
            )
    members = await member_svc.list_by_community(community_id)
    user_ids = [m.user_id for m in members]
    closeness_svc = ClosenessService(db)
    pairs = await closeness_svc.get_pairs_for_users(user_ids)
    return {
        "community_id": str(community_id),
        "members": [{"user_id": str(uid)} for uid in user_ids],
        "pairs": pairs,
    }
