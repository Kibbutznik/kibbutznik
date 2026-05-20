"""Router-level tests for /tkg/*. Seeds rows via TKGService directly, then
hits the HTTP endpoints with the shared `client` fixture.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from kbz.services.tkg_service import TKGService


async def _seed(db_engine):
    """Populate a small graph: two users, one proposal, two SUPPORTED edges."""
    sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    alice = uuid.uuid4()
    bob = uuid.uuid4()
    carol = uuid.uuid4()
    prop = uuid.uuid4()
    community = uuid.uuid4()
    async with sf() as db:
        svc = TKGService(db)
        await svc.upsert_node(alice, "user", label="alice", community_id=community, round_num=1)
        await svc.upsert_node(bob, "user", label="bob", community_id=community, round_num=1)
        await svc.upsert_node(carol, "user", label="carol", community_id=community, round_num=1)
        await svc.upsert_node(prop, "proposal", label="Ban spam", community_id=community, round_num=2)

        await svc.open_edge(alice, prop, "AUTHORED", valid_from_round=2, community_id=community)
        await svc.open_edge(bob, prop, "SUPPORTED", valid_from_round=3, community_id=community)
        await svc.open_edge(carol, prop, "SUPPORTED", valid_from_round=3, community_id=community)
        await svc.open_edge(bob, alice, "ALLIED_WITH", valid_from_round=3, community_id=community)
        await svc.close_edge(bob, alice, "ALLIED_WITH", valid_to_round=7)
        await db.commit()
    return {"alice": alice, "bob": bob, "carol": carol, "prop": prop, "community": community}


@pytest.mark.asyncio
async def test_neighbors_depth1(client, db_engine):
    ids = await _seed(db_engine)
    resp = await client.get(f"/tkg/neighbors/{ids['bob']}", params={"relation": "SUPPORTED"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["dst_id"] == str(ids["prop"])
    assert data[0]["relation"] == "SUPPORTED"
    assert data[0]["neighbor_kind"] == "proposal"
    assert data[0]["neighbor_label"] == "Ban spam"


@pytest.mark.asyncio
async def test_neighbors_at_round_excludes_closed(client, db_engine):
    ids = await _seed(db_engine)
    # ALLIED_WITH was closed at round 7. At round 10 it should not appear.
    resp = await client.get(
        f"/tkg/neighbors/{ids['bob']}",
        params={"relation": "ALLIED_WITH", "at_round": 10},
    )
    assert resp.status_code == 200
    assert resp.json() == []
    # But at round 5 it was still open.
    resp2 = await client.get(
        f"/tkg/neighbors/{ids['bob']}",
        params={"relation": "ALLIED_WITH", "at_round": 5},
    )
    assert len(resp2.json()) == 1


@pytest.mark.asyncio
async def test_timeline_filters_window(client, db_engine):
    ids = await _seed(db_engine)
    resp = await client.get(
        f"/tkg/timeline/{ids['alice']}",
        params={"from_round": 0, "to_round": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    # alice is src of AUTHORED and dst of ALLIED_WITH — both in window.
    relations = {e["relation"] for e in data}
    assert "AUTHORED" in relations
    assert "ALLIED_WITH" in relations


@pytest.mark.asyncio
async def test_prune_endpoint_requires_admin_allowlist(client, db_engine, monkeypatch):
    """Prune is restricted to admin operators via
    KBZ_ADMIN_USER_IDS. Any logged-in user (not on the allowlist)
    gets 403; an admin sees the deletion go through."""
    from kbz.config import settings
    await _seed(db_engine)
    # Log in as a non-admin user.
    r = await client.post(
        "/auth/request-magic-link", json={"email": "tkg-pruner@example.com"},
    )
    body = await client.get(r.json()["link"])
    user_id = body.json()["user"]["user_id"]

    # No allowlist configured → 403.
    monkeypatch.setattr(settings, "admin_user_ids", "")
    resp = await client.delete("/tkg/prune", params={"older_than_round": 100})
    assert resp.status_code == 403

    # Allowlist has someone else → 403.
    monkeypatch.setattr(
        settings, "admin_user_ids", "00000000-0000-0000-0000-000000000099",
    )
    resp = await client.delete("/tkg/prune", params={"older_than_round": 100})
    assert resp.status_code == 403

    # Add this user to the allowlist → 200.
    monkeypatch.setattr(settings, "admin_user_ids", user_id)
    resp = await client.delete("/tkg/prune", params={"older_than_round": 100})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 1}


@pytest.mark.asyncio
async def test_prune_requires_auth(client, db_engine):
    """Pre-fix /tkg/prune was open to anyone — a single anonymous
    DELETE could wipe historical edges from the knowledge graph
    wholesale (older_than_round=999999 deletes every closed edge)."""
    await _seed(db_engine)
    resp = await client.delete(
        "/tkg/prune", params={"older_than_round": 999999},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_semantic_search_limit_is_capped(client):
    """Pre-fix /tkg/semantic_search accepted any `limit` and passed it
    straight into LIMIT :limit — POST limit=10^8 would try to pull the
    whole node table. The schema now caps it at 100, so an absurd limit
    is a clean 422, not a runaway query."""
    resp = await client.post(
        "/tkg/semantic_search",
        json={"query": "anything", "limit": 100_000_000},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_semantic_search_rejects_empty_query(client):
    resp = await client.post("/tkg/semantic_search", json={"query": "", "limit": 5})
    assert resp.status_code == 422
