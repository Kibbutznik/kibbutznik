import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.schemas.member import CommunityMemberResponse, UserMembershipResponse
from kbz.services.member_service import MemberService

router = APIRouter()


@router.get(
    "/communities/{community_id}/members",
    response_model=list[CommunityMemberResponse],
)
async def list_members(
    community_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # Pre-fix this endpoint was unbounded; a community of any size
    # dumped every row, letting an anonymous reader exhaust server
    # memory cheaply. Hard upper bound 1000 per request; clients
    # paginate with offset.
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    svc = MemberService(db)
    return await svc.list_by_community(community_id, limit=limit, offset=offset)


@router.get(
    "/users/{user_id}/communities",
    response_model=list[UserMembershipResponse],
)
async def list_user_communities(
    user_id: uuid.UUID,
    root_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    svc = MemberService(db)
    return await svc.list_by_user(user_id, root_id=root_id)
