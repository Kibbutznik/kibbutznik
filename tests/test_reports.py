"""Tests for the moderation report flow."""
import uuid as _uuid

import pytest

from tests.conftest import create_test_community


async def _login(client, email: str) -> str:
    r = await client.post("/auth/request-magic-link", json={"email": email})
    assert r.status_code == 200
    r = await client.get(r.json()["link"])
    assert r.status_code == 200
    return r.json()["user"]["user_id"]


@pytest.mark.asyncio
async def test_member_can_file_and_list_reports(client):
    """A member files a report against a comment in their own
    community; another member lists open reports and sees it."""
    founder_id = await _login(client, "rep-founder@example.com")
    community = await create_test_community(client, founder_id)

    fake_target = str(_uuid.uuid4())
    resp = await client.post("/reports", json={
        "user_id": founder_id,
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": fake_target,
        "reason_text": "spam",
    })
    assert resp.status_code == 201
    report = resp.json()
    assert report["status"] == "open"
    assert report["target_kind"] == "comment"

    resp = await client.get(
        f"/communities/{community['id']}/reports",
        params={"status": "open"},
    )
    assert resp.status_code == 200
    open_reports = resp.json()
    assert any(r["id"] == report["id"] for r in open_reports)


@pytest.mark.asyncio
async def test_non_member_cannot_file(client):
    """Non-members can't file reports — would otherwise let
    drive-by accounts spam moderation queues."""
    founder_id = await _login(client, "rep-founder2@example.com")
    community = await create_test_community(client, founder_id)

    client.cookies.clear()
    stranger_id = await _login(client, "rep-stranger@example.com")
    resp = await client.post("/reports", json={
        "user_id": stranger_id,
        "community_id": community["id"],
        "target_kind": "user",
        "target_id": founder_id,
        "reason_text": "I just don't like them",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_self_report_against_user_rejected(client):
    """Filing target_kind=user against your own user_id is noise — 400."""
    founder_id = await _login(client, "rep-self@example.com")
    community = await create_test_community(client, founder_id)
    resp = await client.post("/reports", json={
        "user_id": founder_id,
        "community_id": community["id"],
        "target_kind": "user",
        "target_id": founder_id,
        "reason_text": "I'm bad",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resolve_uphold_then_cant_reflip(client):
    """Members can move OPEN → UPHELD or DISMISSED. After that the
    report is locked — re-resolving 400s. This avoids people
    flipping each other's calls back and forth."""
    founder_id = await _login(client, "rep-res-f@example.com")
    community = await create_test_community(client, founder_id)

    fake_target = str(_uuid.uuid4())
    resp = await client.post("/reports", json={
        "user_id": founder_id,
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": fake_target,
        "reason_text": "unkind",
    })
    rid = resp.json()["id"]

    resp = await client.patch(f"/reports/{rid}", json={"status": "upheld"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "upheld"
    assert resp.json()["resolver_user_id"] == founder_id

    # Re-resolve must 400.
    resp = await client.patch(f"/reports/{rid}", json={"status": "dismissed"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resolve_only_by_member(client):
    """A logged-in stranger can't resolve another community's report."""
    founder_id = await _login(client, "rep-res-bystander-f@example.com")
    community = await create_test_community(client, founder_id)
    fake_target = str(_uuid.uuid4())
    resp = await client.post("/reports", json={
        "user_id": founder_id,
        "community_id": community["id"],
        "target_kind": "comment",
        "target_id": fake_target,
        "reason_text": "x",
    })
    rid = resp.json()["id"]

    client.cookies.clear()
    await _login(client, "rep-res-bystander@example.com")
    resp = await client.patch(f"/reports/{rid}", json={"status": "dismissed"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_only_by_member(client):
    """A logged-in stranger can't browse another community's report
    queue — name-and-shame protection."""
    founder_id = await _login(client, "rep-list-f@example.com")
    community = await create_test_community(client, founder_id)

    client.cookies.clear()
    await _login(client, "rep-list-stranger@example.com")
    resp = await client.get(f"/communities/{community['id']}/reports")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invalid_target_kind_rejected_with_422(client):
    founder_id = await _login(client, "rep-bad-kind@example.com")
    community = await create_test_community(client, founder_id)
    resp = await client.post("/reports", json={
        "user_id": founder_id,
        "community_id": community["id"],
        "target_kind": "blueberry",
        "target_id": str(_uuid.uuid4()),
        "reason_text": "x",
    })
    assert resp.status_code == 422
