import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.schemas.proposal import ProposalCreate, ProposalEdit, ProposalResponse, SupportCreate
from kbz.services.proposal_service import ProposalService
from kbz.services.support_service import SupportService

router = APIRouter()


@router.post("/communities/{community_id}/proposals", response_model=ProposalResponse, status_code=201)
async def create_proposal(community_id: uuid.UUID, data: ProposalCreate, db: AsyncSession = Depends(get_db)):
    svc = ProposalService(db)
    proposal = await svc.create(community_id, data)
    return proposal


@router.get("/proposals/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(proposal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = ProposalService(db)
    proposal = await svc.get(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal


@router.get("/communities/{community_id}/proposals", response_model=list[ProposalResponse])
async def list_proposals(
    community_id: uuid.UUID,
    status: str | None = None,
    user_id: uuid.UUID | None = None,
    val_uuid: uuid.UUID | None = None,
    proposal_type: str | None = None,
    pulse_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    svc = ProposalService(db)
    return await svc.list_by_community(
        community_id, status=status, user_id=user_id, val_uuid=val_uuid,
        proposal_type=proposal_type, pulse_id=pulse_id,
    )


@router.patch("/proposals/{proposal_id}/edit", response_model=ProposalResponse)
async def edit_proposal(proposal_id: uuid.UUID, data: ProposalEdit, db: AsyncSession = Depends(get_db)):
    """Edit a proposal's text. Resets ALL support — supporters must re-evaluate."""
    svc = ProposalService(db)
    return await svc.edit_text(
        proposal_id, data.user_id,
        new_text=data.proposal_text, new_val_text=data.val_text,
    )


@router.patch("/proposals/{proposal_id}/submit", response_model=ProposalResponse)
async def submit_proposal(proposal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = ProposalService(db)
    proposal = await svc.submit(proposal_id)
    if not proposal:
        raise HTTPException(status_code=400, detail="Cannot submit proposal")
    return proposal


@router.post("/proposals/{proposal_id}/support", status_code=201)
async def add_support(proposal_id: uuid.UUID, data: SupportCreate, db: AsyncSession = Depends(get_db)):
    svc = SupportService(db)
    await svc.add_proposal_support(proposal_id, data.user_id)
    return {"status": "supported"}


@router.get("/proposals/{proposal_id}/supporters")
async def get_supporters(proposal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = SupportService(db)
    return await svc.get_proposal_supporters(proposal_id)


@router.delete("/proposals/{proposal_id}/support/{user_id}", status_code=200)
async def remove_support(proposal_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = SupportService(db)
    await svc.remove_proposal_support(proposal_id, user_id)
    return {"status": "unsupported"}
