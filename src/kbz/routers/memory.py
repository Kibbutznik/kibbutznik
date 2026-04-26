"""FastAPI router for agent memory CRUD.

Writes (POST/PUT/DELETE-prune) are gated by `enforce_session_matches_body`:
- Agents (no session cookie) pass through with the user_id they
  supply.
- Logged-in humans must operate on their OWN memories.

Without these gates a logged-in user could PUT arbitrary content
into another user's memory, or DELETE all of someone's memories
via /prune. Reads stay open (Big Brother surface).
"""

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import enforce_session_matches_body, get_current_user
from kbz.database import get_db
from kbz.models.agent_memory import AgentMemory
from kbz.models.user import User
from kbz.services.memory_service import MemoryService

router = APIRouter()


# The four values the service queries against and the extractors emit.
# Accepting arbitrary strings leads to silent dead-end rows (e.g. a typoed
# "episodc" that can never be retrieved by the usual type filters).
MemoryType = Literal["episodic", "goal", "relationship", "reflection"]


class MemoryCreate(BaseModel):
    # UUID-typed so a malformed value comes back as a clean 422 instead
    # of crashing the endpoint with `ValueError -> 500` from a manual
    # `uuid.UUID(body.user_id)` call inside the handler.
    user_id: uuid.UUID
    memory_type: MemoryType
    content: str
    importance: float = Field(0.5, ge=0.0, le=1.0)
    category: Optional[str] = None
    round_num: Optional[int] = None
    related_id: Optional[uuid.UUID] = None
    expires_at: Optional[int] = None


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = Field(None, ge=0.0, le=1.0)
    category: Optional[str] = None
    expires_at: Optional[int] = None


@router.post("/memories")
async def add_memory(
    body: MemoryCreate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    enforce_session_matches_body(body.user_id, session_user)
    svc = MemoryService(db)
    return await svc.add_memory(
        user_id=body.user_id,
        memory_type=body.memory_type,
        content=body.content,
        importance=body.importance,
        category=body.category,
        round_num=body.round_num,
        related_id=body.related_id,
        expires_at=body.expires_at,
    )


@router.get("/memories/{user_id}")
async def get_memories(
    user_id: uuid.UUID,
    memory_type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    min_importance: float = Query(0.0, ge=0.0, le=1.0),
    order_by: str = Query("recent"),
    db: AsyncSession = Depends(get_db),
):
    svc = MemoryService(db)
    return await svc.get_memories(
        user_id=user_id,
        memory_type=memory_type,
        limit=limit,
        min_importance=min_importance,
        order_by=order_by,
    )


@router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: uuid.UUID,
    body: MemoryUpdate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    # Resolve the memory's owner first so we can bind to the session
    # before doing any work. Without this, a logged-in human could
    # PUT arbitrary content into anyone else's memory.
    owner = (
        await db.execute(
            select(AgentMemory.user_id).where(AgentMemory.id == memory_id)
        )
    ).scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    enforce_session_matches_body(owner, session_user)

    svc = MemoryService(db)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    result = await svc.update_memory(memory_id, **updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return result


@router.delete("/memories/prune/{user_id}")
async def prune_memories(
    user_id: uuid.UUID,
    current_round: int = Query(..., ge=0),
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    # Pre-fix anyone could DELETE-prune another user's entire memory
    # set with one curl. Bind to the session so logged-in humans
    # can only prune their own memories; agents (no cookie) still
    # pass through with the URL user_id.
    enforce_session_matches_body(user_id, session_user)
    svc = MemoryService(db)
    deleted = await svc.prune(user_id, current_round)
    return {"deleted": deleted}


@router.get("/memories/{user_id}/relationship/{target_user_id}")
async def get_relationship(
    user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    svc = MemoryService(db)
    result = await svc.find_relationship(
        user_id, target_user_id,
    )
    return result or {"detail": "No relationship memory found"}
