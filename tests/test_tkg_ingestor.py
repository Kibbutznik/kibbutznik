"""Ingestor tests — push synthetic events directly to the handler and assert
nodes/edges appear. We bypass event_bus wiring for determinism.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from kbz.models.tkg import TKGEdge, TKGNode, TKGRelation
from kbz.services.event_bus import Event
from kbz.services.tkg_ingestor import TKGIngestor


async def _dispatch(ingestor: TKGIngestor, db_engine, evt: Event):
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await ingestor._handle(db, evt)
        await db.commit()


@pytest.mark.asyncio
async def test_proposal_created_creates_authored_edge(db_engine):
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    ingestor = TKGIngestor(sf)

    community_id = uuid.uuid4()
    user_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    evt = Event(
        event_type="proposal.created",
        community_id=community_id,
        user_id=user_id,
        data={
            "proposal_id": str(proposal_id),
            "proposal_type": "ACTION",
            "proposal_text": "Ban spam bots from general chat",
            "round_num": 3,
        },
    )
    await _dispatch(ingestor, db_engine, evt)

    async with sf() as db:
        user_node = (await db.execute(select(TKGNode).where(TKGNode.id == user_id))).scalar_one()
        prop_node = (await db.execute(select(TKGNode).where(TKGNode.id == proposal_id))).scalar_one()
        assert user_node.kind == "user"
        assert prop_node.kind == "proposal"
        assert "Ban spam bots" in (prop_node.label or "")

        edges = (await db.execute(
            select(TKGEdge).where(
                TKGEdge.src_id == user_id,
                TKGEdge.dst_id == proposal_id,
                TKGEdge.relation == TKGRelation.AUTHORED,
            )
        )).scalars().all()
        assert len(edges) == 1
        assert edges[0].valid_from_round == 3


@pytest.mark.asyncio
async def test_support_cast_and_withdrawn(db_engine):
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    ingestor = TKGIngestor(sf)

    community_id = uuid.uuid4()
    supporter = uuid.uuid4()
    author = uuid.uuid4()
    proposal_id = uuid.uuid4()

    await _dispatch(ingestor, db_engine, Event(
        event_type="support.cast",
        community_id=community_id,
        user_id=supporter,
        data={
            "proposal_id": str(proposal_id),
            "author_id": str(author),
            "round_num": 5,
        },
    ))

    async with sf() as db:
        sup_edge = (await db.execute(
            select(TKGEdge).where(
                TKGEdge.src_id == supporter,
                TKGEdge.dst_id == proposal_id,
                TKGEdge.relation == TKGRelation.SUPPORTED,
            )
        )).scalar_one()
        assert sup_edge.valid_to_round is None
        ally_edge = (await db.execute(
            select(TKGEdge).where(
                TKGEdge.src_id == supporter,
                TKGEdge.dst_id == author,
                TKGEdge.relation == TKGRelation.ALLIED_WITH,
            )
        )).scalar_one()
        assert ally_edge.valid_to_round is None

    # Withdraw support
    await _dispatch(ingestor, db_engine, Event(
        event_type="support.withdrawn",
        community_id=community_id,
        user_id=supporter,
        data={"proposal_id": str(proposal_id), "round_num": 7},
    ))

    async with sf() as db:
        sup_edge = (await db.execute(
            select(TKGEdge).where(
                TKGEdge.src_id == supporter,
                TKGEdge.dst_id == proposal_id,
                TKGEdge.relation == TKGRelation.SUPPORTED,
            )
        )).scalar_one()
        assert sup_edge.valid_to_round == 7


@pytest.mark.asyncio
async def test_unknown_event_type_is_noop(db_engine):
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    ingestor = TKGIngestor(sf)
    # Should not raise or write anything.
    await _dispatch(ingestor, db_engine, Event(
        event_type="proposal.time_traveled",
        data={"anything": "goes"},
    ))
    async with sf() as db:
        rows = (await db.execute(select(TKGNode))).scalars().all()
        assert rows == []
