"""Tests for the cross-entity /search endpoint."""
import pytest

from tests.conftest import create_test_community, create_test_user


async def _accept(client, community_id, user_id, proposal_id):
    await client.patch(f"/proposals/{proposal_id}/submit")
    await client.post(f"/proposals/{proposal_id}/support", json={"user_id": user_id})
    for _ in range(2):
        await client.post(
            f"/communities/{community_id}/pulses/support",
            json={"user_id": user_id},
        )


@pytest.mark.asyncio
async def test_search_finds_community_by_name(client):
    user = await create_test_user(client)
    await create_test_community(client, user["id"], name="Onboarding Collective")
    await create_test_community(client, user["id"], name="Writing Circle")

    resp = await client.get("/search", params={"q": "onbo"})
    assert resp.status_code == 200
    hits = resp.json()
    titles = [h["title"] for h in hits if h["kind"] == "community"]
    assert "Onboarding Collective" in titles
    assert "Writing Circle" not in titles


@pytest.mark.asyncio
async def test_search_finds_statement_text(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "members publish weekly digests of decisions",
    })
    pid = resp.json()["id"]
    await _accept(client, community["id"], user["id"], pid)

    resp = await client.get("/search", params={"q": "digests", "kind": "statement"})
    hits = resp.json()
    assert any(h["kind"] == "statement" and "digests" in h["title"].lower() for h in hits)


@pytest.mark.asyncio
async def test_search_finds_proposal_pitch(client):
    """Search hits the pitch column too — bots and humans often
    explain WHY in pitch, not in proposal_text."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "open governance",
        "pitch": "transparency is non-negotiable for trust at scale",
    })
    resp = await client.get("/search", params={
        "q": "non-negotiable", "kind": "proposal",
    })
    assert resp.status_code == 200
    hits = resp.json()
    assert len(hits) >= 1
    assert any("non-negotiable" in (h["snippet"] or "") for h in hits)


@pytest.mark.asyncio
async def test_search_scoped_by_community_id(client):
    """Statement/proposal hits respect ?community_id — community
    hits stay platform-wide regardless."""
    user = await create_test_user(client)
    a = await create_test_community(client, user["id"], name="Alpha-S")
    b = await create_test_community(client, user["id"], name="Bravo-S")

    # Identical-text proposal in each community.
    for c_id in (a["id"], b["id"]):
        await client.post(f"/communities/{c_id}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": "weekly digest pilot",
        })

    resp = await client.get("/search", params={
        "q": "weekly digest pilot",
        "kind": "proposal",
        "community_id": a["id"],
    })
    hits = resp.json()
    assert all(h["community_id"] == a["id"] for h in hits)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_search_rejects_empty_query(client):
    resp = await client.get("/search", params={"q": "   "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_escapes_like_wildcards(client):
    """A query of '_' or '%' must NOT match every row; the
    wildcards are escaped before being passed to LIKE."""
    user = await create_test_user(client)
    await create_test_community(client, user["id"], name="Pure Letters")
    await create_test_community(client, user["id"], name="With_Underscore")

    # Searching for "_" — pre-fix this would have matched both.
    # Post-fix it only matches the row that literally contains "_".
    resp = await client.get("/search", params={"q": "_", "kind": "community"})
    titles = [h["title"] for h in resp.json()]
    assert "With_Underscore" in titles
    assert "Pure Letters" not in titles
