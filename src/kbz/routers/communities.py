import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
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
