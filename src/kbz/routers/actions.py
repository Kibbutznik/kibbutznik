import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.schemas.action import ActionResponse
from kbz.services.action_service import ActionService

router = APIRouter()


@router.get("/communities/{community_id}/actions", response_model=list[ActionResponse])
async def list_actions(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = ActionService(db)
    return await svc.list_by_parent(community_id)
