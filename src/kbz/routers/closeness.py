import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.services.closeness_service import ClosenessService
from kbz.services.community_service import CommunityService
from kbz.services.member_service import MemberService

router = APIRouter()


@router.get("/communities/{community_id}/closeness")
async def get_community_closeness(
    community_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    # Without this gate, a typo'd or stale community id returns
    # 200 with empty members + pairs — indistinguishable from a real
    # community whose graph happens to be empty. 404 instead.
    if await CommunityService(db).get(community_id) is None:
        raise HTTPException(status_code=404, detail="Community not found")
    member_svc = MemberService(db)
    members = await member_svc.list_by_community(community_id)
    user_ids = [m.user_id for m in members]
    closeness_svc = ClosenessService(db)
    pairs = await closeness_svc.get_pairs_for_users(user_ids)
    return {
        "community_id": str(community_id),
        "members": [{"user_id": str(uid)} for uid in user_ids],
        "pairs": pairs,
    }
