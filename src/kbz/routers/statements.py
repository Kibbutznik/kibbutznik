import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.schemas.statement import StatementResponse
from kbz.services.statement_service import StatementService

router = APIRouter()


@router.get("/communities/{community_id}/statements", response_model=list[StatementResponse])
async def list_statements(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = StatementService(db)
    return await svc.list_by_community(community_id)


@router.get("/statements/{statement_id}", response_model=StatementResponse)
async def get_statement(statement_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = StatementService(db)
    statement = await svc.get(statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")
    return statement
