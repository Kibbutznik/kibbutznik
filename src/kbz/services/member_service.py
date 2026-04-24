import uuid
from types import SimpleNamespace

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import MemberStatus
from kbz.models.bot_profile import BotProfile
from kbz.models.community import Community
from kbz.models.member import Member
from kbz.models.user import User


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

        No-op when the target is not an ACTIVE member of the parent
        community (never joined, already thrown out, etc.) — blindly
        decrementing member_count in those cases would drift the count
        below zero over time.
        """
        from kbz.models.action import Action

        # 1. Remove from the parent community — gated on the target
        # actually being ACTIVE here. A ThrowOut proposal can legitimately
        # target someone who's since left on their own; decrementing
        # member_count again would corrupt the count.
        parent_member = await self.get(community_id, user_id)
        if parent_member is None or parent_member.status != MemberStatus.ACTIVE:
            return
        parent_member.status = MemberStatus.THROWN_OUT
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

    async def list_by_community(self, community_id: uuid.UUID) -> list[SimpleNamespace]:
        """Return active members joined with user_name and the optional
        per-community bot display_name.

        Returns SimpleNamespace objects so callers can use attribute
        access (`m.user_id`) and pydantic MemberResponse can pick up
        user_name / display_name via from_attributes.
        """
        stmt = (
            select(Member, User.user_name, BotProfile.display_name)
            .outerjoin(User, User.id == Member.user_id)
            .outerjoin(
                BotProfile,
                (BotProfile.user_id == Member.user_id)
                & (BotProfile.community_id == Member.community_id),
            )
            .where(
                Member.community_id == community_id,
                Member.status == MemberStatus.ACTIVE,
            )
        )
        rows = await self.db.execute(stmt)
        return [
            SimpleNamespace(
                community_id=m.community_id,
                user_id=m.user_id,
                user_name=user_name,
                display_name=display_name,
                status=m.status,
                seniority=m.seniority,
                joined_at=m.joined_at,
            )
            for m, user_name, display_name in rows.all()
        ]

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        root_id: uuid.UUID | None = None,
    ) -> list[SimpleNamespace]:
        """List active memberships for a user. Each row is enriched with the
        member's community_name and the id of the tree-root community the
        membership ultimately belongs to (a single recursive CTE, so O(1)
        request regardless of tree depth).

        When ``root_id`` is given, only memberships whose tree-root equals
        that value are returned — this is what lets the viewer skip its
        client-side findRoot walk.
        """
        from sqlalchemy import text

        sql = text(
            """
            WITH RECURSIVE ancestors AS (
                SELECT
                    c.id,
                    c.parent_id,
                    c.name,
                    c.id AS origin_id
                FROM communities c
                JOIN members m ON m.community_id = c.id
                WHERE m.user_id = :user_id
                  AND m.status = :active_status
                UNION ALL
                SELECT
                    p.id,
                    p.parent_id,
                    p.name,
                    a.origin_id
                FROM communities p
                JOIN ancestors a
                  ON p.id = a.parent_id
                 AND a.parent_id <> '00000000-0000-0000-0000-000000000000'::uuid
            ),
            roots AS (
                -- A "root" is an ancestor whose parent is NIL.
                SELECT origin_id, id AS root_id
                FROM ancestors
                WHERE parent_id = '00000000-0000-0000-0000-000000000000'::uuid
            )
            SELECT
                m.community_id,
                m.user_id,
                u.user_name,
                m.status,
                m.seniority,
                m.joined_at,
                c.name                AS community_name,
                c.parent_id           AS community_parent_id,
                COALESCE(r.root_id, m.community_id) AS community_root_id
            FROM members m
            JOIN communities c ON c.id = m.community_id
            LEFT JOIN users u   ON u.id = m.user_id
            LEFT JOIN roots r   ON r.origin_id = m.community_id
            WHERE m.user_id = :user_id
              AND m.status  = :active_status
            """
        )
        rows = await self.db.execute(
            sql,
            {"user_id": user_id, "active_status": int(MemberStatus.ACTIVE)},
        )
        out: list[SimpleNamespace] = []
        for row in rows.mappings().all():
            if root_id is not None and row["community_root_id"] != root_id:
                continue
            out.append(
                SimpleNamespace(
                    community_id=row["community_id"],
                    user_id=row["user_id"],
                    user_name=row["user_name"],
                    display_name=None,
                    status=row["status"],
                    seniority=row["seniority"],
                    joined_at=row["joined_at"],
                    community_name=row["community_name"],
                    community_parent_id=row["community_parent_id"],
                    community_root_id=row["community_root_id"],
                )
            )
        return out

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
