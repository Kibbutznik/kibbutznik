"""Invite flow for humans joining a KBZ community.

The invite is a one-shot token bound to a specific community. Claim flow:

    1. Human clicks an invite link:  /invite/{invite_code}
       (handled client-side in the viewer — just passes the code along).
    2. Viewer prompts for email, calls
         POST /invites/claim {invite_code, email}
       which:
         - looks up (and locks) the invite
         - creates-or-fetches the human user for that email
         - creates a Membership proposal on the community (status=DRAFT)
         - submits it so the existing pulse machinery handles the vote
         - issues a magic link for that user and returns the verify URL
         - marks the invite claimed
    3. Human clicks the verify link → session cookie set → they're in the
       viewer as a logged-in human whose Membership proposal is in flight.

Once agents accept the Membership proposal the human becomes an active
member via the existing ExecutionService handler — no new code path needed.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.config import settings
from kbz.enums import ProposalStatus, ProposalType
from kbz.models.auth import Invite
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.user import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _invite_code() -> str:
    # 32 chars of URL-safe randomness — much more than the 64-char column
    # can hold, so truncate (token_urlsafe gives > 32 chars per 24 bytes).
    return secrets.token_urlsafe(24)[:48]


@dataclass
class IssuedInvite:
    invite_id: uuid.UUID
    code: str
    expires_at: datetime
    community_id: uuid.UUID


@dataclass
class ClaimedInvite:
    user: User
    membership_proposal_id: uuid.UUID
    community_id: uuid.UUID


class InviteService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        community_id: uuid.UUID,
        creator_user_id: uuid.UUID | None,
    ) -> IssuedInvite:
        # Validate the community exists — fail early with a useful error
        # instead of letting a foreign-key error bubble up later.
        exists = (
            await self.db.execute(
                select(Community.id).where(Community.id == community_id)
            )
        ).scalar_one_or_none()
        if exists is None:
            raise ValueError("community not found")

        code = _invite_code()
        expires = _now() + timedelta(hours=settings.auth_invite_ttl_hours)
        invite = Invite(
            id=uuid.uuid4(),
            community_id=community_id,
            creator_user_id=creator_user_id,
            invite_code=code,
            expires_at=expires,
        )
        self.db.add(invite)
        await self.db.flush()
        return IssuedInvite(
            invite_id=invite.id,
            code=code,
            expires_at=expires,
            community_id=community_id,
        )

    async def claim(
        self,
        *,
        invite_code: str,
        email: str,
    ) -> ClaimedInvite:
        """Validate + consume an invite, create user, draft Membership proposal.

        Returns the created User and the in-flight Membership proposal id.
        Does NOT issue a magic link here — the router composes that so the
        service stays easy to test.
        """
        if not invite_code:
            raise ValueError("missing invite_code")
        now = _now()

        invite = (
            await self.db.execute(
                select(Invite).where(Invite.invite_code == invite_code)
            )
        ).scalar_one_or_none()
        if invite is None:
            raise ValueError("invite not found")
        if invite.claimed_by_user_id is not None:
            raise ValueError("invite already claimed")
        if invite.expires_at <= now:
            raise ValueError("invite expired")

        # Create-or-fetch the human user (lazy import to avoid a circular
        # dep between services).
        from kbz.services.auth_service import AuthService

        auth_svc = AuthService(self.db)
        user = await auth_svc.get_or_create_human(email)

        # Dedupe: if this user already has an in-flight Membership
        # proposal in this community (filed directly via /proposals
        # OR via a previous invite claim), reuse it instead of
        # creating a second ghost row. ProposalService.create enforces
        # this through DEDUPE_RULES, but we built the Proposal by hand
        # below — so the same gate has to live here.
        from kbz.services.proposal_service import _ACTIVE_DEDUPE_STATUSES
        from sqlalchemy import or_ as _or
        existing = (
            await self.db.execute(
                select(Proposal).where(
                    Proposal.community_id == invite.community_id,
                    Proposal.proposal_type == ProposalType.MEMBERSHIP,
                    Proposal.proposal_status.in_(_ACTIVE_DEDUPE_STATUSES),
                    # Membership proposals can carry the applicant in
                    # `val_uuid` (somebody-proposed-X) OR `user_id`
                    # (self-application). Match either, same as
                    # DEDUPE_RULES["applicant"].
                    _or(
                        Proposal.val_uuid == user.id,
                        Proposal.user_id == user.id,
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Mark the invite consumed so it can't be re-used, but
            # return the EXISTING proposal id rather than minting a
            # second one. The user lands in the same in-flight
            # state either way.
            await self.db.execute(
                update(Invite)
                .where(Invite.id == invite.id, Invite.claimed_by_user_id.is_(None))
                .values(claimed_by_user_id=user.id, claimed_at=now)
            )
            await self.db.flush()
            return ClaimedInvite(
                user=user,
                membership_proposal_id=existing.id,
                community_id=invite.community_id,
            )

        # Draft + submit a Membership proposal so the existing pulse machinery
        # handles the vote. Agents will see it in their next snapshot and
        # vote on it like any other proposal.
        proposal = Proposal(
            id=uuid.uuid4(),
            community_id=invite.community_id,
            user_id=user.id,
            proposal_type=ProposalType.MEMBERSHIP,
            proposal_status=ProposalStatus.OUT_THERE,  # skip Draft → show up in snapshot
            proposal_text=(
                f"{user.user_name} (human, {user.email}) applied via invite link"
            ),
            val_uuid=user.id,
            val_text="",
            age=0,
            support_count=0,
        )
        self.db.add(proposal)

        # Mark invite consumed (atomic-ish — claimed_at being NULL-gated
        # makes concurrent claims safe).
        result = await self.db.execute(
            update(Invite)
            .where(Invite.id == invite.id, Invite.claimed_by_user_id.is_(None))
            .values(claimed_by_user_id=user.id, claimed_at=now)
            .returning(Invite.id)
        )
        claimed = result.scalar_one_or_none()
        if claimed is None:
            raise ValueError("invite already claimed (race)")

        await self.db.flush()

        return ClaimedInvite(
            user=user,
            membership_proposal_id=proposal.id,
            community_id=invite.community_id,
        )
