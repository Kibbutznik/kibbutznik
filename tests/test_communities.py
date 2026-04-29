import uuid

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


@pytest.mark.asyncio
async def test_create_community_rejects_bogus_parent(client):
    """Non-zero parent_id must point at an existing community. Otherwise
    we'd dangle an orphan sub-community that can't be reached from any
    root — the action tree would silently lose a branch."""
    user = await create_test_user(client)
    resp = await client.post("/communities", json={
        "name": "Orphan",
        "founder_user_id": user["id"],
        "parent_id": "11111111-1111-1111-1111-111111111111",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_user_rejects_whitespace_only_name(client):
    """min_length=3 lets '   ' through. Reject at schema."""
    for bad in ("   ", "\t\n ", "    "):
        r = await client.post("/users", json={
            "user_name": bad, "password": "x",
        })
        assert r.status_code == 422, (
            f"user_name={bad!r} should 422; got {r.status_code} {r.text}"
        )


@pytest.mark.asyncio
async def test_create_user_does_not_store_password_hash(client, db):
    """Pre-fix POST /users wrote SHA-256(password) to password_hash —
    unsalted, no key-stretching. Auth uses magic-link tokens; the
    column is never read. Now we discard the password and store '' so
    a DB breach can't turn this column into a credential leak."""
    import hashlib
    from sqlalchemy import select as _select
    from kbz.models.user import User

    r = await client.post("/users", json={
        "user_name": "pwd-discard-test", "password": "supersecret123",
    })
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    # Read straight from the DB.
    user = (
        await db.execute(_select(User).where(User.id == uuid.UUID(uid)))
    ).scalar_one()
    assert user.password_hash == "", (
        f"password_hash should be empty — got {user.password_hash!r}. "
        f"If non-empty, a DB dump leaks credentials via rainbow tables."
    )
    # And specifically, it must NOT be SHA-256 of the password.
    sha = hashlib.sha256(b"supersecret123").hexdigest()
    assert user.password_hash != sha


@pytest.mark.asyncio
async def test_create_community_rejects_control_chars_in_name(client):
    """Pre-fix the validator only blocked whitespace-only names. A
    name with embedded \\n / \\t / \\x00 / \\x7f breaks UI list
    rendering, log lines, and email subjects (which inline the
    community name into outgoing mail). Reject at the schema layer."""
    user = await create_test_user(client)
    for bad in ("good\nname", "tab\there", "\x00null", "DEL\x7fhere"):
        r = await client.post("/communities", json={
            "name": bad, "founder_user_id": user["id"],
        })
        assert r.status_code == 422, (
            f"name={bad!r} should 422; got {r.status_code} {r.text}"
        )


@pytest.mark.asyncio
async def test_create_community_rejects_phantom_founder(client):
    """Pre-fix `CommunityService.create` did not validate
    `founder_user_id` against the users table. Agent paths (no cookie)
    bypass `enforce_session_matches_body`, so any caller could
    materialize a community with a Member row pointing at a
    non-existent user_id — phantom "active member" forever, member_count
    permanently >= 1."""
    bogus = "11111111-1111-1111-1111-111111111111"
    r = await client.post("/communities", json={
        "name": "Phantom",
        "founder_user_id": bogus,
    })
    assert r.status_code == 400, r.text
    assert "does not exist" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_member_list_paginates_and_caps_limit(client):
    """Pre-fix `GET /communities/{id}/members` was unbounded; an
    anon reader could exhaust server memory in a loop. Now: hard
    upper bound 1000, default 200, offset is honored."""
    user_ = await create_test_user(client)
    community = await create_test_community(client, user_["id"])
    r = await client.get(f"/communities/{community['id']}/members?limit=1500")
    assert r.status_code == 422, r.text
    r = await client.get(f"/communities/{community['id']}/members?limit=0")
    assert r.status_code == 422
    r = await client.get(f"/communities/{community['id']}/members?limit=200&offset=0")
    assert r.status_code == 200
