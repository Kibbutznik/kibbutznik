import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import enforce_session_matches_body, get_current_user
from kbz.database import get_db
from kbz.models.user import User
from kbz.schemas.proposal import ProposalCreate, ProposalEdit, ProposalResponse, SupportCreate
from kbz.services.proposal_service import ProposalService
from kbz.services.support_service import SupportService

router = APIRouter()


@router.post("/communities/{community_id}/proposals", response_model=ProposalResponse, status_code=201)
async def create_proposal(
    community_id: uuid.UUID,
    data: ProposalCreate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    enforce_session_matches_body(data.user_id, session_user)
    svc = ProposalService(db)
    proposal = await svc.create(community_id, data)
    return await svc.enrich_one(proposal, community_id)


@router.get("/proposals/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(proposal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = ProposalService(db)
    proposal = await svc.get(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return await svc.enrich_one(proposal)


@router.get("/communities/{community_id}/proposals", response_model=list[ProposalResponse])
async def list_proposals(
    community_id: uuid.UUID,
    status: str | None = None,
    user_id: uuid.UUID | None = None,
    val_uuid: uuid.UUID | None = None,
    proposal_type: str | None = None,
    pulse_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    svc = ProposalService(db)
    proposals = await svc.list_by_community(
        community_id, status=status, user_id=user_id, val_uuid=val_uuid,
        proposal_type=proposal_type, pulse_id=pulse_id,
        limit=limit, offset=offset,
    )
    return await svc.enrich(proposals, community_id)


@router.patch("/proposals/{proposal_id}/edit", response_model=ProposalResponse)
async def edit_proposal(
    proposal_id: uuid.UUID,
    data: ProposalEdit,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    """Edit a proposal's text. Resets ALL support — supporters must re-evaluate."""
    # Without this, a logged-in user could POST body `user_id=<author_id>`
    # and edit someone else's proposal — the service-level check only
    # validates that `data.user_id` matches the author, not that the
    # caller IS that author.
    enforce_session_matches_body(data.user_id, session_user)
    svc = ProposalService(db)
    proposal = await svc.edit_text(
        proposal_id, data.user_id,
        new_text=data.proposal_text, new_val_text=data.val_text,
        new_pitch=data.pitch,
    )
    return await svc.enrich_one(proposal)


@router.patch("/proposals/{proposal_id}/submit", response_model=ProposalResponse)
async def submit_proposal(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    """Promote a draft proposal to OutThere.

    Agents (no session) pass straight through — same as other write paths.
    Logged-in users must be the proposal's author; otherwise one user
    could promote another user's in-progress draft before they were ready.
    """
    svc = ProposalService(db)
    proposal = await svc.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if session_user is not None and proposal.user_id != session_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the proposal's author can submit it",
        )
    proposal = await svc.submit(proposal_id)
    return await svc.enrich_one(proposal)


@router.post("/proposals/{proposal_id}/withdraw", response_model=ProposalResponse)
async def withdraw_proposal(
    proposal_id: uuid.UUID,
    data: SupportCreate,  # reused: body = {"user_id": "..."}
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    """Author cancels their own proposal before quorum.

    Only valid while the proposal is still DRAFT or OUT_THERE — once it's
    ON_THE_AIR, ACCEPTED, REJECTED, or CANCELED, we don't allow retraction
    (too late, or already done). The author's user_id must match.
    """
    from sqlalchemy import select, update

    from kbz.enums import ProposalStatus
    from kbz.models.proposal import Proposal

    # Block session-user spoofing BEFORE the ownership check, otherwise a
    # logged-in Mallory could POST {user_id: alice} and pass the
    # proposal.user_id == data.user_id test simply by echoing Alice's id.
    enforce_session_matches_body(data.user_id, session_user)
    proposal = (
        await db.execute(select(Proposal).where(Proposal.id == proposal_id))
    ).scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.user_id != data.user_id:
        raise HTTPException(status_code=403, detail="Only the author can withdraw")
    if proposal.proposal_status not in (
        ProposalStatus.DRAFT,
        ProposalStatus.OUT_THERE,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot withdraw — proposal is {proposal.proposal_status}",
        )
    await db.execute(
        update(Proposal)
        .where(Proposal.id == proposal_id)
        .values(proposal_status=ProposalStatus.CANCELED)
    )
    # Refund any Membership escrow before committing
    from kbz.enums import ProposalType as _PT
    if proposal.proposal_type == _PT.MEMBERSHIP:
        from kbz.services.wallet_service import WalletService
        await WalletService(db).escrow_refund(proposal.id)
    await db.commit()
    proposal = (
        await db.execute(select(Proposal).where(Proposal.id == proposal_id))
    ).scalar_one()
    return await ProposalService(db).enrich_one(proposal)


@router.post("/proposals/{proposal_id}/support", status_code=201)
async def add_support(
    proposal_id: uuid.UUID,
    data: SupportCreate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    enforce_session_matches_body(data.user_id, session_user)
    svc = SupportService(db)
    await svc.add_proposal_support(proposal_id, data.user_id)
    return {"status": "supported"}


@router.post("/proposals/{proposal_id}/ghost_support", status_code=201)
async def add_ghost_support(proposal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Big Brother viewer support: bumps support_count by 1 with no trace.

    No membership check, no row written to the `supports` table, no user
    recorded. The only side effect is incrementing `proposals.support_count`.
    """
    from sqlalchemy import select, update
    from kbz.models.proposal import Proposal
    from kbz.enums import ProposalStatus
    result = await db.execute(select(Proposal).where(Proposal.id == proposal_id))
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.proposal_status not in (ProposalStatus.OUT_THERE, ProposalStatus.ON_THE_AIR):
        raise HTTPException(status_code=400, detail="Proposal is not in a supportable state")
    await db.execute(
        update(Proposal)
        .where(Proposal.id == proposal_id)
        .values(support_count=Proposal.support_count + 1)
    )
    await db.commit()
    return {"status": "ghost_supported"}


@router.get("/proposals/{proposal_id}/supporters")
async def get_supporters(proposal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    # Without this gate, a typo'd or stale proposal_id returns 200 with
    # an empty list — indistinguishable from a real proposal nobody
    # has supported yet. 404 makes the difference visible to clients.
    if await ProposalService(db).get(proposal_id) is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    svc = SupportService(db)
    return await svc.get_proposal_supporters(proposal_id)


@router.delete("/proposals/{proposal_id}/support/{user_id}", status_code=200)
async def remove_support(
    proposal_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    # Without the session check, a logged-in user could DELETE any other
    # member's support row purely by URL — a one-shot way to shave points
    # off a rival proposal before a pulse.
    enforce_session_matches_body(user_id, session_user)
    svc = SupportService(db)
    await svc.remove_proposal_support(proposal_id, user_id)
    return {"status": "unsupported"}
