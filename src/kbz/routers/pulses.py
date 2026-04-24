import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import enforce_session_matches_body, get_current_user
from kbz.database import get_db
from kbz.models.user import User
from kbz.schemas.pulse import PulseResponse, PulseSupportCreate
from kbz.services.pulse_service import PulseService
from kbz.services.support_service import SupportService

router = APIRouter()


@router.get("/communities/{community_id}/pulses", response_model=list[PulseResponse])
async def list_pulses(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = PulseService(db)
    return await svc.list_by_community(community_id)


@router.get("/pulses/{pulse_id}", response_model=PulseResponse)
async def get_pulse(pulse_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = PulseService(db)
    pulse = await svc.get(pulse_id)
    if not pulse:
        raise HTTPException(status_code=404, detail="Pulse not found")
    return pulse


@router.post("/communities/{community_id}/pulses/support", status_code=201)
async def add_pulse_support(
    community_id: uuid.UUID,
    data: PulseSupportCreate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    enforce_session_matches_body(data.user_id, session_user)
    svc = SupportService(db)
    result = await svc.add_pulse_support(community_id, data.user_id)
    return result


@router.get("/pulses/{pulse_id}/supporters")
async def get_pulse_supporters(pulse_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = SupportService(db)
    return await svc.get_pulse_supporters(pulse_id)


@router.delete("/communities/{community_id}/pulses/support/{user_id}", status_code=200)
async def remove_pulse_support(
    community_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    # Same reasoning as proposal remove_support: a DELETE that mutates
    # someone else's governance vote must not be reachable without being
    # that user.
    enforce_session_matches_body(user_id, session_user)
    svc = SupportService(db)
    await svc.remove_pulse_support(community_id, user_id)
    return {"status": "unsupported"}
