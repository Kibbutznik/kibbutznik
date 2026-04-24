import pytest
from tests.conftest import create_test_user, create_test_community

NIL_UUID = "00000000-0000-0000-0000-000000000000"


@pytest.mark.asyncio
async def test_create_community(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "My Kibbutz")
    assert community["name"] == "My Kibbutz"
    assert community["status"] == 1
    assert community["member_count"] == 1
    assert community["parent_id"] == NIL_UUID


@pytest.mark.asyncio
async def test_community_has_variables(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "VarTest")
    resp = await client.get(f"/communities/{community['id']}/variables")
    assert resp.status_code == 200
    variables = resp.json()["variables"]
    assert variables["PulseSupport"] == "50"
    assert variables["ProposalSupport"] == "25"
    assert variables["Name"] == "VarTest"
    assert variables["MaxAge"] == "2"


@pytest.mark.asyncio
async def test_community_has_initial_pulse(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.get(f"/communities/{community['id']}/pulses")
    assert resp.status_code == 200
    pulses = resp.json()
    assert len(pulses) == 1
    assert pulses[0]["status"] == 0  # Next pulse
    assert pulses[0]["threshold"] == 1  # ceil(1 * 50/100) = 1


@pytest.mark.asyncio
async def test_founder_is_member(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.get(f"/communities/{community['id']}/members")
    assert resp.status_code == 200
    members = resp.json()
    assert len(members) == 1
    assert members[0]["user_id"] == user["id"]
    assert members[0]["seniority"] == 0


@pytest.mark.asyncio
async def test_get_community(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.get(f"/communities/{community['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Community"


@pytest.mark.asyncio
async def test_community_not_found(client):
    resp = await client.get("/communities/00000000-0000-0000-0000-000000000001")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_community_rejects_empty_name(client):
    """communities.name is NOT NULL String(255); empty string should
    422 at the edge, not end up as a blank row or fall back to the
    server-side default."""
    user = await create_test_user(client)
    resp = await client.post("/communities", json={
        "name": "", "founder_user_id": user["id"],
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_community_rejects_oversized_name(client):
    """Names over the String(255) column used to surface as a 500 via
    DataError; the schema cap turns that into a clean 422."""
    user = await create_test_user(client)
    resp = await client.post("/communities", json={
        "name": "x" * 300, "founder_user_id": user["id"],
    })
    assert resp.status_code == 422
