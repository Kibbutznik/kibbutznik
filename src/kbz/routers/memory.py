"""FastAPI router for agent memory CRUD."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.services.memory_service import MemoryService

router = APIRouter()


class MemoryCreate(BaseModel):
    user_id: str
    memory_type: str  # episodic | goal | relationship | reflection
    content: str
    importance: float = Field(0.5, ge=0.0, le=1.0)
    category: Optional[str] = None
    round_num: Optional[int] = None
    related_id: Optional[str] = None
    expires_at: Optional[int] = None


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = Field(None, ge=0.0, le=1.0)
    category: Optional[str] = None
    expires_at: Optional[int] = None


@router.post("/memories")
async def add_memory(body: MemoryCreate, db: AsyncSession = Depends(get_db)):
    svc = MemoryService(db)
    return await svc.add_memory(
        user_id=uuid.UUID(body.user_id),
        memory_type=body.memory_type,
        content=body.content,
        importance=body.importance,
        category=body.category,
        round_num=body.round_num,
        related_id=uuid.UUID(body.related_id) if body.related_id else None,
        expires_at=body.expires_at,
    )


@router.get("/memories/{user_id}")
async def get_memories(
    user_id: str,
    memory_type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    min_importance: float = Query(0.0, ge=0.0, le=1.0),
    order_by: str = Query("recent"),
    db: AsyncSession = Depends(get_db),
):
    svc = MemoryService(db)
    return await svc.get_memories(
        user_id=uuid.UUID(user_id),
        memory_type=memory_type,
        limit=limit,
        min_importance=min_importance,
        order_by=order_by,
    )


@router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    db: AsyncSession = Depends(get_db),
):
    svc = MemoryService(db)
    updates = body.model_dump(exclude_none=True)
    result = await svc.update_memory(uuid.UUID(memory_id), **updates)
    if result is None:
        return {"detail": "Memory not found or no valid fields to update"}
    return result


@router.delete("/memories/prune/{user_id}")
async def prune_memories(
    user_id: str,
    current_round: int = Query(..., ge=0),
    db: AsyncSession = Depends(get_db),
):
    svc = MemoryService(db)
    deleted = await svc.prune(uuid.UUID(user_id), current_round)
    return {"deleted": deleted}


@router.get("/memories/{user_id}/relationship/{target_user_id}")
async def get_relationship(
    user_id: str,
    target_user_id: str,
    db: AsyncSession = Depends(get_db),
):
    svc = MemoryService(db)
    result = await svc.find_relationship(
        uuid.UUID(user_id), uuid.UUID(target_user_id),
    )
    return result or {"detail": "No relationship memory found"}
