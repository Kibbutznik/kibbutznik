"""Unit tests for TKGService — idempotency, open/close, prune."""

import uuid

import pytest

from kbz.services.tkg_service import TKGService


@pytest.mark.asyncio
async def test_upsert_node_idempotent(db):
    svc = TKGService(db)
    nid = uuid.uuid4()
    await svc.upsert_node(nid, "user", label="alice", round_num=1)
    await svc.upsert_node(nid, "user", label="alice-v2", round_num=5,
                           attrs={"role": "founder"})
    await db.commit()

    from sqlalchemy import select
    from kbz.models.tkg import TKGNode
    row = (await db.execute(select(TKGNode).where(TKGNode.id == nid))).scalar_one()
    assert row.label == "alice-v2"
    assert row.first_seen_round == 1
    assert row.last_seen_round == 5
    assert row.attrs.get("role") == "founder"


@pytest.mark.asyncio
async def test_open_edge_idempotent_bumps_weight(db):
    svc = TKGService(db)
    src, dst = uuid.uuid4(), uuid.uuid4()
    await svc.upsert_node(src, "user", round_num=1)
    await svc.upsert_node(dst, "proposal", round_num=1)

    id1 = await svc.open_edge(src, dst, "SUPPORTED", valid_from_round=1)
    id2 = await svc.open_edge(src, dst, "SUPPORTED", valid_from_round=1)
    assert id1 == id2  # idempotent

    await db.commit()

    from sqlalchemy import select
    from kbz.models.tkg import TKGEdge
    edge = (await db.execute(select(TKGEdge).where(TKGEdge.id == id1))).scalar_one()
    assert edge.weight >= 2.0


@pytest.mark.asyncio
async def test_close_edge_then_prune(db):
    svc = TKGService(db)
    src, dst = uuid.uuid4(), uuid.uuid4()
    await svc.upsert_node(src, "user", round_num=1)
    await svc.upsert_node(dst, "proposal", round_num=1)

    await svc.open_edge(src, dst, "SUPPORTED", valid_from_round=1)
    closed = await svc.close_edge(src, dst, "SUPPORTED", valid_to_round=5)
    assert closed == 1
    await db.commit()

    # Closed at 5, prune older_than=10 → deletes it.
    pruned = await svc.prune_closed_edges(older_than_round=10)
    assert pruned == 1
    await db.commit()

    # Closed-but-recent edge survives prune.
    await svc.open_edge(src, dst, "ALLIED_WITH", valid_from_round=20)
    await svc.close_edge(src, dst, "ALLIED_WITH", valid_to_round=25)
    await db.commit()
    pruned2 = await svc.prune_closed_edges(older_than_round=10)
    assert pruned2 == 0


@pytest.mark.asyncio
async def test_add_embedding_upsert(db):
    svc = TKGService(db)
    nid = uuid.uuid4()
    await svc.upsert_node(nid, "proposal", round_num=1)
    await svc.add_embedding(nid, "first content", [0.1] * 768, round_num=1)
    await svc.add_embedding(nid, "second content", [0.2] * 768, round_num=2)
    await db.commit()

    from sqlalchemy import select
    from kbz.models.tkg import TKGEmbedding
    row = (await db.execute(select(TKGEmbedding).where(TKGEmbedding.node_id == nid))).scalar_one()
    assert row.content == "second content"
    assert row.round_num == 2
