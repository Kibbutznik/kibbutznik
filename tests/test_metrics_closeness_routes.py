"""Route-level tests for /metrics/community/{id} and
/communities/{id}/closeness — focused on the 404-on-unknown
behavior that used to return 200 with empty/zero data."""
import pytest

from tests.conftest import create_test_community, create_test_user


@pytest.mark.asyncio
async def test_metrics_404_on_unknown_community(client):
    """Unknown community id used to return 200 with all-zero
    metrics, indistinguishable from a real community that just
    happens to have no activity. 404 makes the difference legible."""
    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await client.get(f"/metrics/community/{bogus}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_closeness_404_on_unknown_community(client):
    """Same shape: unknown community id used to return 200 with
    empty members + pairs. Now 404."""
    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await client.get(f"/communities/{bogus}/closeness")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_metrics_200_on_known_community(client):
    """Sanity check: a real, freshly-created community still
    returns metrics (with member_count=1 from the founder)."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.get(f"/metrics/community/{community['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["community_id"] == community["id"]
    assert body["member_count"] == 1


@pytest.mark.asyncio
async def test_closeness_200_on_known_community(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.get(f"/communities/{community['id']}/closeness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["community_id"] == community["id"]
    assert len(body["members"]) == 1
