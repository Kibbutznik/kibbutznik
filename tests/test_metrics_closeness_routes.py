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


def test_is_observer_recognizes_big_brother():
    """Unit test on the auth helper. End-to-end auth flow in this app
    is magic-link only, so route-level coverage of the gate is left
    for manual smoke + the prod curl. This locks in that the helper
    matches exactly the canonical Big Brother user_name and nothing
    that resembles it."""
    from types import SimpleNamespace
    from kbz.auth_deps import is_observer, OBSERVER_USER_NAME

    assert OBSERVER_USER_NAME == "Big Brother"
    assert is_observer(None) is False
    assert is_observer(SimpleNamespace(user_name="Big Brother")) is True
    assert is_observer(SimpleNamespace(user_name="big brother")) is False
    assert is_observer(SimpleNamespace(user_name="Big Brother Jr")) is False
    assert is_observer(SimpleNamespace(user_name="")) is False
    assert is_observer(SimpleNamespace(user_name="Marcus")) is False
