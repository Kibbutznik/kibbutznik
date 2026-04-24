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
async def test_variables_and_children_404_on_unknown_community(client):
    """/variables and /children on a bogus community used to return 200
    with empty payloads, making missing communities look identical to
    real-but-empty ones. Both must 404 instead."""
    bogus = "11111111-1111-1111-1111-111111111111"
    resp = await client.get(f"/communities/{bogus}/variables")
    assert resp.status_code == 404
    resp = await client.get(f"/communities/{bogus}/children")
    assert resp.status_code == 404


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
async def test_community_members_response_shape(client):
    """`/communities/{id}/members` should NOT leak the user-membership
    enrichment fields (community_name, community_parent_id,
    community_root_id). The caller already knows the community."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.get(f"/communities/{community['id']}/members")
    assert resp.status_code == 200
    row = resp.json()[0]
    for dead in ("community_name", "community_parent_id", "community_root_id"):
        assert dead not in row, f"{dead} should not appear on CommunityMemberResponse"
    for kept in ("user_name", "display_name", "status", "seniority"):
        assert kept in row


@pytest.mark.asyncio
async def test_user_communities_response_shape(client):
    """`/users/{id}/communities` should carry community_* enrichment fields
    but NOT display_name — it spans multiple communities and the bot's
    per-community display_name is not meaningful cross-community."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.get(f"/users/{user['id']}/communities")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows, "founder should have at least one membership"
    row = next(r for r in rows if r["community_id"] == community["id"])
    assert "display_name" not in row
    for kept in ("community_name", "community_parent_id", "community_root_id"):
        assert kept in row
    assert row["community_name"] == community["name"]


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
