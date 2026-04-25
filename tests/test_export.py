"""Tests for the data export endpoints."""
import pytest

from tests.conftest import create_test_community


async def _login(client, email):
    r = await client.post("/auth/request-magic-link", json={"email": email})
    assert r.status_code == 200
    r = await client.get(r.json()["link"])
    assert r.status_code == 200
    return r.json()["user"]["user_id"]


@pytest.mark.asyncio
async def test_community_export_includes_main_entities(client):
    """A community export bundle includes the community itself,
    its variables, members, statements, proposals, supports, and
    proposal-attached comments."""
    user_id = await _login(client, "exp-c@example.com")
    community = await create_test_community(client, user_id)

    # File a proposal so the export has something interesting.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user_id,
        "proposal_type": "AddStatement",
        "proposal_text": "we ship every Friday",
        "pitch": "rhythm beats heroics",
    })
    pid = resp.json()["id"]
    await client.patch(f"/proposals/{pid}/submit")
    await client.post(f"/proposals/{pid}/support", json={"user_id": user_id})

    # Drop a comment too.
    await client.post(f"/entities/proposal/{pid}/comments", json={
        "user_id": user_id,
        "comment_text": "+1 on the cadence",
    })

    resp = await client.get(f"/communities/{community['id']}/export")
    assert resp.status_code == 200
    bundle = resp.json()
    assert bundle["community"]["id"] == community["id"]
    assert any(v["name"] == "PulseSupport" for v in bundle["variables"])
    assert any(m["user_id"] == user_id for m in bundle["members"])
    assert any(p["id"] == pid for p in bundle["proposals"])
    assert any(s["proposal_id"] == pid for s in bundle["supports"])
    assert any(
        c["entity_id"] == pid and "+1 on the cadence" in c["comment_text"]
        for c in bundle["comments_on_proposals"]
    )


@pytest.mark.asyncio
async def test_community_export_requires_membership(client):
    """A logged-in stranger gets 403 — community exports are
    member-only."""
    founder_id = await _login(client, "exp-f@example.com")
    community = await create_test_community(client, founder_id)
    client.cookies.clear()
    await _login(client, "exp-stranger@example.com")
    resp = await client.get(f"/communities/{community['id']}/export")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_community_export_404_unknown(client):
    await _login(client, "exp-404@example.com")
    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await client.get(f"/communities/{bogus}/export")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_my_export_returns_authored_and_supports(client):
    """`/users/me/export` returns the logged-in user's own slice
    of the data: profile + memberships + proposals authored +
    supports cast + comments posted."""
    user_id = await _login(client, "exp-me@example.com")
    community = await create_test_community(client, user_id)

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user_id,
        "proposal_type": "AddStatement",
        "proposal_text": "be excellent",
    })
    pid = resp.json()["id"]
    await client.patch(f"/proposals/{pid}/submit")
    await client.post(f"/proposals/{pid}/support", json={"user_id": user_id})

    resp = await client.get("/users/me/export")
    assert resp.status_code == 200
    bundle = resp.json()
    assert bundle["user"]["id"] == user_id
    assert any(m["community_id"] == community["id"] for m in bundle["memberships"])
    assert any(p["id"] == pid for p in bundle["proposals_authored"])
    assert any(s["proposal_id"] == pid for s in bundle["supports_cast"])


@pytest.mark.asyncio
async def test_my_export_requires_session(client):
    resp = await client.get("/users/me/export")
    assert resp.status_code in (401, 403)
