import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.schemas.comment import CommentCreate, CommentResponse, ScoreUpdate
from kbz.services.comment_service import CommentService

router = APIRouter()


@router.post("/entities/{entity_type}/{entity_id}/comments", response_model=CommentResponse, status_code=201)
async def add_comment(entity_type: str, entity_id: uuid.UUID, data: CommentCreate, db: AsyncSession = Depends(get_db)):
    svc = CommentService(db)
    return await svc.add_comment(entity_id, entity_type, data)


@router.get("/entities/{entity_type}/{entity_id}/comments", response_model=list[CommentResponse])
async def get_comments(entity_type: str, entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CommentService(db)
    return await svc.get_comments(entity_id, entity_type)


@router.post("/comments/{comment_id}/score")
async def update_score(comment_id: uuid.UUID, data: ScoreUpdate, db: AsyncSession = Depends(get_db)):
    svc = CommentService(db)
    await svc.update_score(comment_id, data.delta)
    return {"status": "updated"}
