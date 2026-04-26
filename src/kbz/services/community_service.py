import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import DEFAULT_VARIABLES, CommunityStatus, PulseStatus, MemberStatus
from kbz.models.community import Community
from kbz.models.member import Member
from kbz.models.pulse import Pulse
from kbz.models.variable import Variable
from kbz.schemas.community import CommunityCreate


class CommunityService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: CommunityCreate) -> Community:
        community_id = uuid.uuid4()

        # Validate parent_id: non-zero parent must point at an existing
        # community. Without this, callers can spawn orphan sub-communities
        # under any random UUID and dangle the action tree.
        ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
        if data.parent_id != ZERO_UUID:
            parent_exists = (
                await self.db.execute(
                    select(Community.id).where(Community.id == data.parent_id)
                )
            ).scalar_one_or_none()
            if parent_exists is None:
                raise ValueError(f"parent_id {data.parent_id} does not exist")

        # 1. Create community
        community = Community(
            id=community_id,
            parent_id=data.parent_id,
            name=data.name,
            status=CommunityStatus.ACTIVE,
            member_count=1,
        )
        self.db.add(community)

        # 2. Copy all default variables
        # `enable_financial=True` on the create payload sets the
        # Financial variable to 'internal' eagerly, skipping the
        # ChangeVariable proposal dance (founder is sole member at
        # t=0 — a vote would be theater).
        for var_name, var_value in DEFAULT_VARIABLES.items():
            if var_name == "Name":
                value = data.name
            elif var_name == "Financial" and getattr(data, "enable_financial", False):
                value = "internal"
            else:
                value = var_value
            self.db.add(Variable(community_id=community_id, name=var_name, value=value))

        # 3. Add founding user as member
        member = Member(
            community_id=community_id,
            user_id=data.founder_user_id,
            status=MemberStatus.ACTIVE,
            seniority=0,
        )
        self.db.add(member)

        # 4. Create initial Next pulse
        threshold = max(1, math.ceil(1 * int(DEFAULT_VARIABLES["PulseSupport"]) / 100))
        pulse = Pulse(
            id=uuid.uuid4(),
            community_id=community_id,
            status=PulseStatus.NEXT,
            support_count=0,
            threshold=threshold,
        )
        self.db.add(pulse)

        # 5. Seed the primordial ArtifactContainer (with its Plan
        # artifact) for EVERY community — root AND child action.
        # Pre-fix this only ran for root communities (parent_id ==
        # ZERO_UUID). That left action communities (created by
        # accepted AddAction proposals via _exec_add_action →
        # community_svc.create with a non-zero parent_id) as bare
        # rooms with no container at all. Members who joined via
        # JoinAction had nowhere to file artifacts: CreateArtifact
        # requires a val_uuid pointing at a container, and there
        # wasn't one. The only path to seed an action's container
        # was DelegateArtifact from the parent — so an Action that
        # nobody parented work down into just sat idle ("empty
        # pulses one by one"). Now every community boots with the
        # same Plan + container so its members can start working
        # immediately.
        from kbz.services.artifact_service import ArtifactService
        await ArtifactService(self.db).create_root_container(
            community_id,
            mission=data.initial_artifact_mission,
            founder_user_id=data.founder_user_id,
        )

        await self.db.flush()
        await self.db.refresh(community)
        return community

    async def get(self, community_id: uuid.UUID) -> Community | None:
        result = await self.db.execute(select(Community).where(Community.id == community_id))
        return result.scalar_one_or_none()

    async def get_variables(self, community_id: uuid.UUID) -> dict[str, str]:
        result = await self.db.execute(
            select(Variable).where(Variable.community_id == community_id)
        )
        variables = result.scalars().all()
        return {v.name: v.value for v in variables}

    async def get_variable_value(self, community_id: uuid.UUID, name: str) -> str | None:
        result = await self.db.execute(
            select(Variable).where(
                Variable.community_id == community_id,
                Variable.name == name,
            )
        )
        var = result.scalar_one_or_none()
        return var.value if var else None

    async def get_children(self, community_id: uuid.UUID) -> list[Community]:
        result = await self.db.execute(
            select(Community).where(Community.parent_id == community_id)
        )
        return list(result.scalars().all())

    async def get_member_count(self, community_id: uuid.UUID) -> int:
        community = await self.get(community_id)
        return community.member_count if community else 0
