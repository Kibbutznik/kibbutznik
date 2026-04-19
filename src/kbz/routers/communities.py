import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.models.community import Community
from kbz.schemas.community import CommunityCreate, CommunityResponse, CommunityVariablesResponse
from kbz.services.community_service import CommunityService

router = APIRouter()


@router.post("", response_model=CommunityResponse, status_code=201)
async def create_community(data: CommunityCreate, db: AsyncSession = Depends(get_db)):
    svc = CommunityService(db)
    community = await svc.create(data)
    await db.commit()
    await db.refresh(community)
    return community


_ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@router.get("", response_model=list[CommunityResponse])
async def list_communities(
    q: str | None = Query(default=None, description="case-insensitive name substring"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_actions: bool = Query(
        default=False,
        description="include child action-communities (parent_id != ZERO_UUID). "
                    "Default false — the human product only browses root communities.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """List (root) communities for the /browse page.

    Deliberately returns only root kibbutzim by default so humans don't
    see the thousands of sub-action communities the simulation spawns.
    """
    where = []
    if not include_actions:
        where.append(Community.parent_id == _ZERO_UUID)
    if q:
        where.append(func.lower(Community.name).like(f"%{q.lower()}%"))
    stmt = (
        select(Community)
        .where(and_(*where) if where else True)
        .order_by(Community.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return rows


@router.get("/{community_id}", response_model=CommunityResponse)
async def get_community(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CommunityService(db)
    community = await svc.get(community_id)
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    return community


@router.get("/{community_id}/variables", response_model=CommunityVariablesResponse)
async def get_variables(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CommunityService(db)
    variables = await svc.get_variables(community_id)
    return CommunityVariablesResponse(community_id=community_id, variables=variables)


@router.get("/{community_id}/children", response_model=list[CommunityResponse])
async def get_children(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CommunityService(db)
    return await svc.get_children(community_id)
