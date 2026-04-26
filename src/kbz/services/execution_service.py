import logging
import uuid

from sqlalchemy import update, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import ProposalType, CommunityStatus, DEFAULT_VARIABLES, StatementStatus, ProposalStatus
from kbz.models.action import Action
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.statement import Statement
from kbz.models.variable import Variable
from kbz.services.artifact_service import ArtifactService, ArtifactServiceError, parse_ordered_uuid_list
from kbz.services.member_service import MemberService

logger = logging.getLogger(__name__)


class ExecutionService:
    """Dispatches accepted proposals to their type-specific execution logic."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def execute_proposal(self, proposal: Proposal) -> None:
        ptype = ProposalType(proposal.proposal_type)
        handler = self._handlers.get(ptype)
        if handler:
            await handler(self, proposal)

    async def _exec_membership(self, proposal: Proposal) -> None:
        from kbz.services.wallet_service import (
            WalletService, OWNER_COMMUNITY,
        )
        member_svc = MemberService(self.db)
        target_user_id = proposal.val_uuid or proposal.user_id
        await member_svc.create(proposal.community_id, target_user_id)

        # If the community is financial AND the applicant opened an
        # escrow at proposal-create time, release it into the
        # community wallet. Safe for the non-financial case — the
        # escrow lookup just returns None.
        svc = WalletService(self.db)
        if await svc.is_financial(proposal.community_id):
            community_wallet = await svc.get_or_create(
                OWNER_COMMUNITY, proposal.community_id,
            )
            await svc.escrow_release(proposal.id, community_wallet)

    async def _exec_throw_out(self, proposal: Proposal) -> None:
        member_svc = MemberService(self.db)
        if proposal.val_uuid:
            await member_svc.throw_out(proposal.community_id, proposal.val_uuid)

    async def _exec_add_statement(self, proposal: Proposal) -> None:
        stmt = Statement(
            id=uuid.uuid4(),
            community_id=proposal.community_id,
            statement_text=proposal.proposal_text,
            status=StatementStatus.ACTIVE,
        )
        self.db.add(stmt)
        await self.db.flush()

    async def _exec_remove_statement(self, proposal: Proposal) -> None:
        if proposal.val_uuid:
            # Scope the UPDATE to the proposal's own community. Without
            # this, an accepted RemoveStatement in community A that
            # carried `val_uuid` pointing at a statement in community B
            # would silently REMOVE B's statement — community A has no
            # business deleting B's text.
            await self.db.execute(
                update(Statement)
                .where(
                    Statement.id == proposal.val_uuid,
                    Statement.community_id == proposal.community_id,
                )
                .values(status=StatementStatus.REMOVED)
            )
            await self.db.flush()

    async def _exec_replace_statement(self, proposal: Proposal) -> None:
        # Remove old statement — same cross-community guard as
        # _exec_remove_statement: only mark REMOVED if the target
        # actually belongs to this community. If the guard rejects
        # the old row we still skip creating a successor below so
        # we don't dangle a prev_statement_id at a foreign row.
        old_removed = False
        if proposal.val_uuid:
            res = await self.db.execute(
                update(Statement)
                .where(
                    Statement.id == proposal.val_uuid,
                    Statement.community_id == proposal.community_id,
                )
                .values(status=StatementStatus.REMOVED)
            )
            old_removed = (res.rowcount or 0) > 0
            if not old_removed:
                # Cross-community val_uuid (or unknown id) — abort the
                # whole replace rather than silently inserting a new
                # statement that references nothing.
                return
        # Create new statement referencing old one
        stmt = Statement(
            id=uuid.uuid4(),
            community_id=proposal.community_id,
            statement_text=proposal.val_text or proposal.proposal_text,
            status=StatementStatus.ACTIVE,
            prev_statement_id=proposal.val_uuid,
        )
        self.db.add(stmt)
        await self.db.flush()

    async def _exec_change_variable(self, proposal: Proposal) -> None:
        # proposal_text holds the variable name; strip any appended pitch text
        # (agents may have appended their reason after a newline)
        var_name = proposal.proposal_text.split("\n")[0].strip()
        var_value = proposal.val_text
        if var_name and var_value:
            await self.db.execute(
                update(Variable)
                .where(
                    Variable.community_id == proposal.community_id,
                    Variable.name == var_name,
                )
                .values(value=var_value)
            )
            await self.db.flush()

    async def _exec_add_action(self, proposal: Proposal) -> None:
        from kbz.services.community_service import CommunityService
        from kbz.schemas.community import CommunityCreate

        community_svc = CommunityService(self.db)
        action_name = proposal.val_text or proposal.proposal_text or "New Action"

        # Create child community for the action
        child = await community_svc.create(
            CommunityCreate(
                name=action_name,
                founder_user_id=proposal.user_id,
                parent_id=proposal.community_id,
            )
        )

        # Create action record
        action = Action(
            action_id=child.id,
            parent_community_id=proposal.community_id,
            status=CommunityStatus.ACTIVE,
        )
        self.db.add(action)
        await self.db.flush()

    async def _exec_end_action(self, proposal: Proposal) -> None:
        if not proposal.val_uuid:
            return
        # Parent → child-action only, same shape as the Funding guard.
        # Without this, an accepted EndAction in community A whose
        # val_uuid pointed at an action under community B would
        # silently mark B's action + sub-community INACTIVE *and*
        # sweep its wallet up to A. Refuse cross-tree targets.
        action_parent = (
            await self.db.execute(
                select(Action.parent_community_id).where(
                    Action.action_id == proposal.val_uuid
                )
            )
        ).scalar_one_or_none()
        if action_parent is None or action_parent != proposal.community_id:
            logger.warning(
                "EndAction %s val_uuid %s is not a direct child action of community %s — refused",
                proposal.id, proposal.val_uuid, proposal.community_id,
            )
            return
        ended_action_id = str(proposal.val_uuid)

        # Sweep the ending action's wallet balance up to its parent
        # community BEFORE marking the action inactive. WalletService
        # no-ops if the parent community isn't financial.
        from kbz.services.wallet_service import WalletService
        await WalletService(self.db).sweep_action_to_parent(
            proposal.val_uuid, proposal_id=proposal.id,
        )

        # Mark the action and its sub-community as inactive
        await self.db.execute(
            update(Action)
            .where(Action.action_id == proposal.val_uuid)
            .values(status=CommunityStatus.INACTIVE)
        )
        await self.db.execute(
            update(Community)
            .where(Community.id == proposal.val_uuid)
            .values(status=CommunityStatus.INACTIVE)
        )

        # Cancel any active DelegateArtifact proposals in the PARENT community
        # that target this now-ended action (val_text = action community_id).
        # Also cancel any JoinAction proposals for this action (val_uuid = action community_id).
        active_statuses = (ProposalStatus.OUT_THERE.value, ProposalStatus.ON_THE_AIR.value)
        orphan_result = await self.db.execute(
            select(Proposal).where(
                Proposal.community_id == proposal.community_id,
                Proposal.proposal_status.in_(active_statuses),
                or_(
                    # DelegateArtifact → val_text is the target action community_id
                    (Proposal.proposal_type == ProposalType.DELEGATE_ARTIFACT.value) &
                    (Proposal.val_text == ended_action_id),
                    # JoinAction → val_uuid is the target action community_id
                    (Proposal.proposal_type == ProposalType.JOIN_ACTION.value) &
                    (Proposal.val_uuid == proposal.val_uuid),
                )
            )
        )
        orphans = orphan_result.scalars().all()
        from datetime import datetime as _dt, timezone as _tz
        _decided_now = _dt.now(_tz.utc)
        for orphan in orphans:
            orphan.proposal_status = ProposalStatus.CANCELED.value
            orphan.decided_at = _decided_now
            logger.info(
                "Auto-canceled %s proposal %s targeting ended action %s",
                orphan.proposal_type, orphan.id, ended_action_id,
            )

        # Also cancel any in-flight proposals INSIDE the now-ended
        # community. Without this, those proposals would sit in
        # OutThere/OnTheAir forever — the new ProposalService.create
        # gate prevents NEW filings, and pulse_service stops
        # processing INACTIVE communities, so they have no path to
        # resolve. Cancel them here to keep state consistent and
        # let the audit log reflect that EndAction terminated them.
        inside_result = await self.db.execute(
            select(Proposal).where(
                Proposal.community_id == proposal.val_uuid,
                Proposal.proposal_status.in_(active_statuses),
            )
        )
        for inside in inside_result.scalars().all():
            inside.proposal_status = ProposalStatus.CANCELED.value
            inside.decided_at = _decided_now
            # Membership proposals open an escrow at create time
            # (see proposal_service.py) when the community is
            # financial AND membershipFee > 0. The other cancel
            # paths (pulse age-out, applicant withdraw, rejection)
            # all refund here too — without a parallel refund on
            # auto-cancel-via-EndAction, the applicant's credits
            # stay locked in the escrow wallet forever after the
            # action terminates. escrow_refund is a no-op when
            # there's no escrow, so this is safe for non-financial
            # action communities and for non-Membership types.
            if inside.proposal_type == ProposalType.MEMBERSHIP.value:
                from kbz.services.wallet_service import WalletService
                await WalletService(self.db).escrow_refund(inside.id)
            logger.info(
                "Auto-canceled %s proposal %s inside ended community %s",
                inside.proposal_type, inside.id, ended_action_id,
            )

        await self.db.flush()

    async def _exec_join_action(self, proposal: Proposal) -> None:
        if not proposal.val_uuid:
            return
        # Parent → child-action only. JoinAction in community A naming
        # an action that belongs to B's tree would otherwise let A's
        # vote add the proposer to one of B's actions — bypassing B's
        # governance over its own membership.
        action_parent = (
            await self.db.execute(
                select(Action.parent_community_id).where(
                    Action.action_id == proposal.val_uuid
                )
            )
        ).scalar_one_or_none()
        if action_parent is None or action_parent != proposal.community_id:
            logger.warning(
                "JoinAction %s val_uuid %s is not a direct child action of community %s — refused",
                proposal.id, proposal.val_uuid, proposal.community_id,
            )
            return
        member_svc = MemberService(self.db)
        await member_svc.create(proposal.val_uuid, proposal.user_id)

    async def _exec_set_membership_handler(self, proposal: Proposal) -> None:
        if proposal.val_uuid:
            await self.db.execute(
                update(Variable)
                .where(
                    Variable.community_id == proposal.community_id,
                    Variable.name == "membershipHandler",
                )
                .values(value=str(proposal.val_uuid))
            )
            await self.db.flush()

    # ---------- Finance module handlers (opt-in via `Financial` var) ------
    #
    # Every handler short-circuits (log + no-op) when the proposal's
    # community isn't financial, so a misfiled proposal can't corrupt
    # ledger state. Real work funnels through WalletService, which
    # enforces FOR-UPDATE locks + balance invariants at each step.

    async def _exec_funding(self, proposal: Proposal) -> None:
        """Parent community → child action. `val_uuid` = target action,
        `val_text` = amount (stringified decimal)."""
        from kbz.services.wallet_service import (
            WalletService, FinancialModuleDisabledError, InsufficientFundsError,
            OWNER_ACTION, OWNER_COMMUNITY,
        )

        if not proposal.val_uuid or not (proposal.val_text or "").strip():
            logger.warning("Funding %s missing val_uuid or val_text", proposal.id)
            return
        # Funding is parent → child-action only. Without this guard,
        # an accepted Funding in community A whose val_uuid pointed at
        # an action under community B would transfer A's credits into
        # B's action wallet, bypassing B's governance over its own
        # action tree. Refuse if val_uuid isn't a direct child of A.
        action_row = (
            await self.db.execute(
                select(Action.parent_community_id).where(
                    Action.action_id == proposal.val_uuid
                )
            )
        ).scalar_one_or_none()
        if action_row is None or action_row != proposal.community_id:
            logger.warning(
                "Funding %s val_uuid %s is not a direct child action of community %s — refused",
                proposal.id, proposal.val_uuid, proposal.community_id,
            )
            return
        svc = WalletService(self.db)
        if not await svc.is_financial(proposal.community_id):
            logger.info(
                "Funding %s short-circuited — community %s not financial",
                proposal.id, proposal.community_id,
            )
            return
        try:
            src = await svc.get_or_create(OWNER_COMMUNITY, proposal.community_id)
            dst = await svc.get_or_create(OWNER_ACTION, proposal.val_uuid)
            await svc.transfer(
                src, dst, proposal.val_text,
                proposal_id=proposal.id,
                memo=f"Funding: {(proposal.proposal_text or '')[:120]}",
            )
        except (FinancialModuleDisabledError, InsufficientFundsError, ValueError) as e:
            logger.warning("Funding %s failed: %s", proposal.id, e)

    async def _exec_payment(self, proposal: Proposal) -> None:
        """Leaf action → external world. `val_text` = amount. The leaf
        constraint is also enforced at proposal-creation time in
        ProposalService so this is a defense in depth."""
        from kbz.services.wallet_service import (
            WalletService, FinancialModuleDisabledError, InsufficientFundsError,
            OWNER_ACTION, OWNER_COMMUNITY,
        )

        if not (proposal.val_text or "").strip():
            logger.warning("Payment %s missing amount (val_text)", proposal.id)
            return
        svc = WalletService(self.db)
        if not await svc.is_financial(proposal.community_id):
            logger.info(
                "Payment %s short-circuited — community %s not financial",
                proposal.id, proposal.community_id,
            )
            return
        # `community_id` on a sub-action's proposal IS the action's own
        # community. But if this proposal was filed against the root
        # community (not inside an action tree), Payment is still
        # allowed — we burn from the root wallet.
        #
        # Filter on `status == ACTIVE` — an EndAction'd sub-action
        # leaves its Action row in place at status=INACTIVE for audit.
        # Without the status filter, closing the last child action
        # would leave the parent permanently unable to file Payment
        # because the dead-but-present row keeps tripping this check.
        has_active_children = (
            await self.db.execute(
                select(Action).where(
                    Action.parent_community_id == proposal.community_id,
                    Action.status == CommunityStatus.ACTIVE,
                )
            )
        ).first() is not None
        if has_active_children:
            logger.warning(
                "Payment %s refused — community %s has active sub-actions "
                "(leaf-only rule)", proposal.id, proposal.community_id,
            )
            return
        try:
            src = await svc.get_or_create(OWNER_COMMUNITY, proposal.community_id)
            await svc.burn(
                src, proposal.val_text,
                proposal_id=proposal.id,
                memo=f"Payment: {(proposal.proposal_text or '')[:160]}",
            )
        except (FinancialModuleDisabledError, InsufficientFundsError, ValueError) as e:
            logger.warning("Payment %s failed: %s", proposal.id, e)

    async def _exec_pay_back(self, proposal: Proposal) -> None:
        """Inverse of Payment — accepted PayBack proposal mints credits
        into the community wallet (e.g. a refund, a reversal). In
        Phase 1 we model this as a mint authorized by proposal id
        (via `webhook_event='proposal.payback'` placeholder, since
        the schema CHECK requires external_ref OR proposal_id)."""
        from kbz.services.wallet_service import (
            WalletService, FinancialModuleDisabledError, OWNER_COMMUNITY,
        )

        if not (proposal.val_text or "").strip():
            return
        svc = WalletService(self.db)
        if not await svc.is_financial(proposal.community_id):
            return
        try:
            dst = await svc.get_or_create(OWNER_COMMUNITY, proposal.community_id)
            await svc.mint(
                dst, proposal.val_text,
                webhook_event="proposal.payback",
                external_ref=str(proposal.id),
                memo=f"PayBack: {(proposal.proposal_text or '')[:120]}",
            )
        except (FinancialModuleDisabledError, ValueError) as e:
            logger.warning("PayBack %s failed: %s", proposal.id, e)

    async def _exec_dividend(self, proposal: Proposal) -> None:
        """Split `val_text` amount equally among active members."""
        from decimal import Decimal, InvalidOperation
        from kbz.services.wallet_service import (
            WalletService, FinancialModuleDisabledError, InsufficientFundsError,
            OWNER_COMMUNITY, OWNER_USER,
        )

        if not (proposal.val_text or "").strip():
            return
        svc = WalletService(self.db)
        if not await svc.is_financial(proposal.community_id):
            return
        try:
            amount = Decimal(proposal.val_text)
        except (InvalidOperation, ValueError, TypeError):
            logger.warning("Dividend %s: bad amount %r", proposal.id, proposal.val_text)
            return
        # Reject Decimal("Infinity") / Decimal("NaN") / non-positive
        # at the source. Without this guard, `share.quantize(...)`
        # on an infinite/NaN amount raises InvalidOperation that
        # bubbles up through pulse_service.execute_pulse and
        # crashes the entire pulse — half-processed proposals get
        # left in inconsistent states.
        if not amount.is_finite() or amount <= 0:
            logger.warning(
                "Dividend %s: non-positive or non-finite amount %r — refused",
                proposal.id, proposal.val_text,
            )
            return
        member_svc = MemberService(self.db)
        members = await member_svc.list_by_community(proposal.community_id)
        if not members:
            return
        share = (amount / Decimal(len(members))).quantize(Decimal("0.000001"))
        if share <= 0:
            return
        try:
            src = await svc.get_or_create(OWNER_COMMUNITY, proposal.community_id)
        except FinancialModuleDisabledError:
            return
        for m in members:
            dst = await svc.get_or_create(OWNER_USER, m.user_id, gate=False)
            try:
                await svc.transfer(
                    src, dst, share,
                    proposal_id=proposal.id,
                    memo=f"Dividend {proposal.id}",
                )
            except InsufficientFundsError:
                logger.warning(
                    "Dividend %s: ran out of funds mid-distribution at member %s",
                    proposal.id, m.user_id,
                )
                break

    # ---------- Artifact / ArtifactContainer handlers ----------

    async def _exec_create_artifact(self, proposal: Proposal) -> None:
        if not proposal.val_uuid:
            logger.warning("CreateArtifact %s missing val_uuid (container_id)", proposal.id)
            return
        # Same per-community guard as Edit/Remove/CommitArtifact.
        # Without it, an accepted CreateArtifact in community A
        # whose val_uuid pointed at a container in B would let A
        # inject an Artifact into B's container. The new Artifact
        # row would carry container.community_id (i.e. B's id),
        # but B never authorized it.
        from kbz.models.artifact_container import ArtifactContainer
        container_cid = (
            await self.db.execute(
                select(ArtifactContainer.community_id).where(
                    ArtifactContainer.id == proposal.val_uuid
                )
            )
        ).scalar_one_or_none()
        if container_cid is None or container_cid != proposal.community_id:
            logger.warning(
                "CreateArtifact %s val_uuid %s targets a foreign or missing container — refused",
                proposal.id, proposal.val_uuid,
            )
            return
        try:
            await ArtifactService(self.db).create_artifact(
                container_id=proposal.val_uuid,
                content="",
                title=proposal.val_text or proposal.proposal_text or "Untitled",
                author_user_id=proposal.user_id,
                proposal_id=proposal.id,
            )
        except ArtifactServiceError as e:
            logger.warning("CreateArtifact %s failed: %s", proposal.id, e)

    async def _artifact_belongs_to(
        self, artifact_id: uuid.UUID, community_id: uuid.UUID,
    ) -> bool:
        """Used by Edit/Remove artifact executors to refuse cross-community
        targets — without this, an accepted EditArtifact in community A
        whose val_uuid pointed at an artifact in community B would let A
        rewrite B's text. The guard is here in the executor (rather than
        deep inside ArtifactService) so the per-community-scope check
        lives next to the proposal that authorized it."""
        from kbz.models.artifact import Artifact
        row = (
            await self.db.execute(
                select(Artifact.community_id).where(Artifact.id == artifact_id)
            )
        ).scalar_one_or_none()
        return row is not None and row == community_id

    async def _exec_edit_artifact(self, proposal: Proposal) -> None:
        if not proposal.val_uuid:
            logger.warning("EditArtifact %s missing val_uuid (artifact_id)", proposal.id)
            return
        if not await self._artifact_belongs_to(proposal.val_uuid, proposal.community_id):
            logger.warning(
                "EditArtifact %s val_uuid %s targets a foreign or missing artifact — refused",
                proposal.id, proposal.val_uuid,
            )
            return
        try:
            await ArtifactService(self.db).edit_artifact(
                artifact_id=proposal.val_uuid,
                new_content=proposal.proposal_text or "",
                new_title=proposal.val_text if proposal.val_text else None,
                author_user_id=proposal.user_id,
                proposal_id=proposal.id,
            )
        except ArtifactServiceError as e:
            logger.warning("EditArtifact %s failed: %s", proposal.id, e)

    async def _exec_remove_artifact(self, proposal: Proposal) -> None:
        if not proposal.val_uuid:
            logger.warning("RemoveArtifact %s missing val_uuid (artifact_id)", proposal.id)
            return
        if not await self._artifact_belongs_to(proposal.val_uuid, proposal.community_id):
            logger.warning(
                "RemoveArtifact %s val_uuid %s targets a foreign or missing artifact — refused",
                proposal.id, proposal.val_uuid,
            )
            return
        try:
            await ArtifactService(self.db).remove_artifact(proposal.val_uuid)
        except ArtifactServiceError as e:
            logger.warning("RemoveArtifact %s failed: %s", proposal.id, e)

    async def _exec_delegate_artifact(self, proposal: Proposal) -> None:
        if not proposal.val_uuid or not proposal.val_text:
            logger.warning(
                "DelegateArtifact %s missing val_uuid (artifact_id) or val_text (action_community_id)",
                proposal.id,
            )
            return
        try:
            target = uuid.UUID(proposal.val_text.strip())
        except ValueError:
            logger.warning(
                "DelegateArtifact %s val_text is not a valid UUID: %r",
                proposal.id,
                proposal.val_text,
            )
            return
        try:
            await ArtifactService(self.db).delegate(
                source_artifact_id=proposal.val_uuid,
                target_action_community_id=target,
                delegating_proposal=proposal,
            )
        except ArtifactServiceError as e:
            logger.error("DelegateArtifact %s failed: %s", proposal.id, e, exc_info=True)

    async def _exec_commit_artifact(self, proposal: Proposal) -> None:
        if not proposal.val_uuid:
            logger.warning("CommitArtifact %s missing val_uuid (container_id)", proposal.id)
            return
        # Same cross-community defense as the Edit/Remove artifact
        # executors. An accepted CommitArtifact in community A
        # whose val_uuid pointed at a container in B would let A:
        #   - mark B's container as COMMITTED (status flip),
        #   - overwrite B's committed_content with whatever A
        #     packaged (an empty val_text wipes it to ""),
        #   - and for delegated containers, materialize a Draft
        #     EditArtifact in B's parent community without their
        #     authorization.
        # Refuse if the container's community_id doesn't match
        # the proposal's community_id.
        from kbz.models.artifact_container import ArtifactContainer
        container_cid = (
            await self.db.execute(
                select(ArtifactContainer.community_id).where(
                    ArtifactContainer.id == proposal.val_uuid
                )
            )
        ).scalar_one_or_none()
        if container_cid is None or container_cid != proposal.community_id:
            logger.warning(
                "CommitArtifact %s val_uuid %s targets a foreign or missing container — refused",
                proposal.id, proposal.val_uuid,
            )
            return
        try:
            ordered_ids = parse_ordered_uuid_list(proposal.val_text or "")
            await ArtifactService(self.db).commit_container(
                container_id=proposal.val_uuid,
                ordered_artifact_ids=ordered_ids,
                committer_user_id=proposal.user_id,
            )
        except ArtifactServiceError as e:
            logger.warning("CommitArtifact %s failed: %s", proposal.id, e)

    _handlers = {
        ProposalType.MEMBERSHIP: _exec_membership,
        ProposalType.THROW_OUT: _exec_throw_out,
        ProposalType.ADD_STATEMENT: _exec_add_statement,
        ProposalType.REMOVE_STATEMENT: _exec_remove_statement,
        ProposalType.REPLACE_STATEMENT: _exec_replace_statement,
        ProposalType.CHANGE_VARIABLE: _exec_change_variable,
        ProposalType.ADD_ACTION: _exec_add_action,
        ProposalType.END_ACTION: _exec_end_action,
        ProposalType.JOIN_ACTION: _exec_join_action,
        ProposalType.FUNDING: _exec_funding,
        ProposalType.PAYMENT: _exec_payment,
        ProposalType.PAY_BACK: _exec_pay_back,
        ProposalType.DIVIDEND: _exec_dividend,
        ProposalType.SET_MEMBERSHIP_HANDLER: _exec_set_membership_handler,
        ProposalType.CREATE_ARTIFACT: _exec_create_artifact,
        ProposalType.EDIT_ARTIFACT: _exec_edit_artifact,
        ProposalType.REMOVE_ARTIFACT: _exec_remove_artifact,
        ProposalType.DELEGATE_ARTIFACT: _exec_delegate_artifact,
        ProposalType.COMMIT_ARTIFACT: _exec_commit_artifact,
    }
