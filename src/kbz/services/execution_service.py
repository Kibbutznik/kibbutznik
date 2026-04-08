import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import ProposalType, CommunityStatus, DEFAULT_VARIABLES, StatementStatus
from kbz.models.action import Action
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.statement import Statement
from kbz.models.variable import Variable
from kbz.services.member_service import MemberService


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
        member_svc = MemberService(self.db)
        target_user_id = proposal.val_uuid or proposal.user_id
        await member_svc.create(proposal.community_id, target_user_id)

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
        if proposal.val_uuid:
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

    async def _exec_funding(self, proposal: Proposal) -> None:
        # Placeholder for funding logic
        pass

    async def _exec_payment(self, proposal: Proposal) -> None:
        # Placeholder for payment logic
        pass

    async def _exec_pay_back(self, proposal: Proposal) -> None:
        # Placeholder for payback logic
        pass

    async def _exec_dividend(self, proposal: Proposal) -> None:
        # Placeholder for dividend logic
        pass

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
    }
