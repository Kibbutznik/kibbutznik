import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import MemberStatus
from kbz.models.community import Community
from kbz.models.member import Member


class MemberService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, community_id: uuid.UUID, user_id: uuid.UUID) -> Member:
        # Dedup: check if a member record already exists for this (community, user)
        existing = await self.get(community_id, user_id)
        if existing is not None and existing.status == MemberStatus.ACTIVE:
            return existing  # already an active member — no-op

        if existing is not None:
            # Previously thrown out — reactivate
            existing.status = MemberStatus.ACTIVE
            existing.seniority = 0
            await self.db.execute(
                update(Community)
                .where(Community.id == community_id)
                .values(member_count=Community.member_count + 1)
            )
            await self.db.flush()
            return existing

        # Brand new member
        member = Member(
            community_id=community_id,
            user_id=user_id,
            status=MemberStatus.ACTIVE,
            seniority=0,
        )
        self.db.add(member)

        # Increment member count
        await self.db.execute(
            update(Community)
            .where(Community.id == community_id)
            .values(member_count=Community.member_count + 1)
        )
        await self.db.flush()
        return member

    async def throw_out(self, community_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Remove a member from a community AND all its sub-communities (actions).

        When a member is thrown out of a parent community, they are also
        removed from every action (child community) they belong to.
        This mirrors the real-world rule: leaving the org means leaving its teams.
        """
        from kbz.models.action import Action

        # 1. Remove from the parent community
        await self.db.execute(
            update(Member)
            .where(Member.community_id == community_id, Member.user_id == user_id)
            .values(status=MemberStatus.THROWN_OUT)
        )
        await self.db.execute(
            update(Community)
            .where(Community.id == community_id)
            .values(member_count=Community.member_count - 1)
        )

        # 2. Cascade: remove from all child action communities
        action_rows = await self.db.execute(
            select(Action.action_id).where(Action.parent_community_id == community_id)
        )
        child_ids = [row[0] for row in action_rows.all()]
        for child_id in child_ids:
            child_member = await self.get(child_id, user_id)
            if child_member is not None and child_member.status == MemberStatus.ACTIVE:
                child_member.status = MemberStatus.THROWN_OUT
                await self.db.execute(
                    update(Community)
                    .where(Community.id == child_id)
                    .values(member_count=Community.member_count - 1)
                )

        await self.db.flush()

    async def get(self, community_id: uuid.UUID, user_id: uuid.UUID) -> Member | None:
        result = await self.db.execute(
            select(Member).where(
                Member.community_id == community_id,
                Member.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def is_active_member(self, community_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        member = await self.get(community_id, user_id)
        return member is not None and member.status == MemberStatus.ACTIVE

    async def list_by_community(self, community_id: uuid.UUID) -> list[Member]:
        result = await self.db.execute(
            select(Member).where(
                Member.community_id == community_id,
                Member.status == MemberStatus.ACTIVE,
            )
        )
        return list(result.scalars().all())

    async def list_by_user(self, user_id: uuid.UUID) -> list[Member]:
        result = await self.db.execute(
            select(Member).where(
                Member.user_id == user_id,
                Member.status == MemberStatus.ACTIVE,
            )
        )
        return list(result.scalars().all())

    async def increment_seniority(self, community_id: uuid.UUID) -> None:
        await self.db.execute(
            update(Member)
            .where(
                Member.community_id == community_id,
                Member.status == MemberStatus.ACTIVE,
            )
            .values(seniority=Member.seniority + 1)
        )
        await self.db.flush()
