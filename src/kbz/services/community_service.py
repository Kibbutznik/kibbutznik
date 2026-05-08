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

        # Validate founder_user_id points at a real User. Pre-fix the
        # `enforce_session_matches_body` layer let agents (no cookie)
        # supply ANY UUID, so a community could materialize with a
        # Member row pointing at a non-existent user_id (no FK on
        # members) — the community had a phantom "active member"
        # forever and member_count was permanently stuck >= 1.
        from kbz.models.user import User
        founder_exists = (
            await self.db.execute(
                select(User.id).where(User.id == data.founder_user_id)
            )
        ).scalar_one_or_none()
        if founder_exists is None:
            raise ValueError(
                f"founder_user_id {data.founder_user_id} does not exist"
            )

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
        # Visibility is ROOT-only: action sub-communities inherit
        # the root's Visibility at read time. Seeding it on actions
        # would create the false impression that an action can be
        # "public" while its parent is "private" (the read gate
        # walks up to the root, so it can't).
        is_root = (data.parent_id == ZERO_UUID)
        for var_name, var_value in DEFAULT_VARIABLES.items():
            if var_name == "Visibility" and not is_root:
                continue
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

        # 5. If this is a root community (no parent), seed the primordial ArtifactContainer.
        if data.parent_id == ZERO_UUID:
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

    # ─── Visibility helpers ───────────────────────────────────────
    # Visibility lives on the ROOT community only. Action sub-
    # communities inherit. These helpers walk the parent_id chain
    # so callers don't have to reimplement the climb.

    async def get_root_id(self, community_id: uuid.UUID) -> uuid.UUID:
        """Walk parent_id up to the root. Returns the input id if it
        IS root. Cycle-safe (max 64 hops); will raise ValueError if
        we somehow loop or hit a missing community."""
        ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
        cid = community_id
        for _ in range(64):
            row = (
                await self.db.execute(
                    select(Community.id, Community.parent_id).where(
                        Community.id == cid
                    )
                )
            ).one_or_none()
            if row is None:
                raise ValueError(
                    f"get_root_id: community {cid} not found while "
                    f"climbing from {community_id}"
                )
            _, parent = row
            if parent == ZERO_UUID:
                return cid
            cid = parent
        raise ValueError(
            f"get_root_id: walked 64 levels from {community_id} "
            f"without hitting root — cycle?"
        )

    async def get_effective_visibility(self, community_id: uuid.UUID) -> str:
        """Visibility for `community_id` = the Visibility variable on
        its root. Falls back to 'public' if the root row is missing
        (legacy communities created before this variable existed).
        Always lower-cased; caller can compare to the literal strings
        'public' / 'unlisted' / 'private'."""
        root_id = await self.get_root_id(community_id)
        raw = await self.get_variable_value(root_id, "Visibility")
        if raw is None:
            return "public"
        return raw.strip().lower() or "public"
