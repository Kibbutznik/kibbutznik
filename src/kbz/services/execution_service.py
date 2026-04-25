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
            await self.db.execute(
                update(Statement)
                .where(Statement.id == proposal.val_uuid)
                .values(status=StatementStatus.REMOVED)
            )
            await self.db.flush()

    async def _exec_replace_statement(self, proposal: Proposal) -> None:
        # Remove old statement
        if proposal.val_uuid:
            await self.db.execute(
                update(Statement)
                .where(Statement.id == proposal.val_uuid)
                .values(status=StatementStatus.REMOVED)
            )
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
        for orphan in orphans:
            orphan.proposal_status = ProposalStatus.CANCELED.value
            logger.info(
                "Auto-canceled %s proposal %s targeting ended action %s",
                orphan.proposal_type, orphan.id, ended_action_id,
            )

        await self.db.flush()

    async def _exec_join_action(self, proposal: Proposal) -> None:
        if proposal.val_uuid:
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
        from decimal import Decimal
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
        except Exception:
            logger.warning("Dividend %s: bad amount %r", proposal.id, proposal.val_text)
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

    async def _exec_edit_artifact(self, proposal: Proposal) -> None:
        if not proposal.val_uuid:
            logger.warning("EditArtifact %s missing val_uuid (artifact_id)", proposal.id)
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
