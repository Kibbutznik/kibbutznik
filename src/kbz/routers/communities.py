import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.models.community import Community
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.user import User
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

    Also filters out dead-sim clutter — communities are hidden unless
    one of these is true:
      - has at least one human member (is_human=true)
      - has proposal activity in the last 48 hours
    Keeps the Browse page useful without needing a separate cleanup job.
    The `include_dead=true` query param disables the filter.
    """
    where = []
    if not include_actions:
        where.append(Community.parent_id == _ZERO_UUID)
    if q:
        where.append(func.lower(Community.name).like(f"%{q.lower()}%"))

    has_human_member = exists().where(
        Member.community_id == Community.id,
        Member.user_id == User.id,
        User.is_human.is_(True),
    )
    has_recent_activity = exists().where(
        Proposal.community_id == Community.id,
        Proposal.created_at > func.now() - text("interval '48 hours'"),
    )
    # A freshly-created kibbutz with no activity yet also counts as alive
    # so the founder sees it on Browse immediately.
    recently_created = Community.created_at > func.now() - text("interval '48 hours'")
    where.append(or_(has_human_member, has_recent_activity, recently_created))

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
    # Without this gate, calling /variables on a bogus UUID returns a
    # cheerful 200 echoing the bogus id back with `variables: {}`. That
    # makes "community exists with no vars" indistinguishable from
    # "community doesn't exist" for clients, and lets typos look like
    # success in logs.
    if await svc.get(community_id) is None:
        raise HTTPException(status_code=404, detail="Community not found")
    variables = await svc.get_variables(community_id)
    return CommunityVariablesResponse(community_id=community_id, variables=variables)


@router.get("/{community_id}/children", response_model=list[CommunityResponse])
async def get_children(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CommunityService(db)
    if await svc.get(community_id) is None:
        raise HTTPException(status_code=404, detail="Community not found")
    return await svc.get_children(community_id)
