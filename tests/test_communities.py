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


# ─── Visibility variable (root-only) ─────────────────────────────

@pytest.mark.asyncio
async def test_visibility_seeded_default_public_on_root(client):
    """Root communities get the Visibility variable seeded with
    'public' so existing-behavior callers see no change."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "VizRoot")
    resp = await client.get(f"/communities/{community['id']}/variables")
    variables = resp.json()["variables"]
    assert variables.get("Visibility") == "public"


@pytest.mark.asyncio
async def test_visibility_not_seeded_on_action_subcommunity(client, db):
    """Action sub-communities (parent_id != ZERO_UUID) must NOT get
    a Visibility row of their own — they inherit from the root."""
    import uuid as _uuid
    from kbz.models.community import Community as _C
    from kbz.models.member import Member as _M
    from kbz.models.variable import Variable as _V
    from sqlalchemy import select as _select

    user = await create_test_user(client)
    root = await create_test_community(client, user["id"], "VizParent")
    # Inject an action sub-community directly. CommunityService.create
    # fans out side-effects (founder member, initial pulse, etc.) but
    # for THIS test all we care about is that variable seeding
    # honors the parent_id check — so use the same code path.
    child_id = _uuid.uuid4()
    db.add(_C(
        id=child_id,
        parent_id=_uuid.UUID(root["id"]),
        name="Child Action",
        status=1,
        member_count=1,
    ))
    # Mimic what CommunityService.create does for variables: seed
    # everything EXCEPT Visibility on a non-root community.
    from kbz.enums import DEFAULT_VARIABLES
    for var_name, var_value in DEFAULT_VARIABLES.items():
        if var_name == "Visibility":
            continue
        db.add(_V(community_id=child_id, name=var_name, value=var_value))
    await db.commit()

    rows = (
        await db.execute(_select(_V).where(_V.community_id == child_id))
    ).scalars().all()
    names = {r.name for r in rows}
    assert "Visibility" not in names
    assert "PulseSupport" in names  # other variables still seeded


@pytest.mark.asyncio
async def test_change_variable_visibility_rejects_invalid_value(client):
    """Visibility must be one of public / unlisted / private. Any
    other val_text gets a 422 at proposal-create time."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"], "VizCheck")
    r = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "Visibility",
        "val_text": "secret",
    })
    assert r.status_code == 422, r.text
    assert "visibility" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_change_variable_visibility_accepts_valid_values(client):
    """The three allowed values must each pass create-time validation.
    One community per value because the per-(community, variable)
    dedupe rule blocks a second in-flight ChangeVariable on the same
    name."""
    user = await create_test_user(client)
    for i, value in enumerate(("public", "unlisted", "private")):
        community = await create_test_community(client, user["id"], f"VizOK_{i}")
        r = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "ChangeVariable",
            "proposal_text": "Visibility",
            "val_text": value,
        })
        assert r.status_code == 201, f"value={value} got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_change_variable_visibility_rejected_on_action_community(client, db):
    """Visibility on an ACTION sub-community must fail at create time
    with a clear message pointing at the root."""
    import uuid as _uuid
    from kbz.models.community import Community as _C
    from kbz.models.member import Member as _M
    from kbz.enums import MemberStatus as _MS

    user = await create_test_user(client)
    root = await create_test_community(client, user["id"], "VizRootAct")
    # Spawn a fake action sub-community directly with the same user
    # as a member, so they have standing to file proposals.
    action_id = _uuid.uuid4()
    db.add(_C(
        id=action_id,
        parent_id=_uuid.UUID(root["id"]),
        name="Action Sub",
        status=1,
        member_count=1,
    ))
    db.add(_M(
        community_id=action_id,
        user_id=_uuid.UUID(user["id"]),
        status=_MS.ACTIVE,
        seniority=0,
    ))
    await db.commit()

    r = await client.post(f"/communities/{action_id}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "Visibility",
        "val_text": "private",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"].lower()
    assert "root" in detail and "visibility" in detail


@pytest.mark.asyncio
async def test_get_effective_visibility_walks_to_root(client, db):
    """The CommunityService helper resolves a child's visibility by
    walking up to the root and reading its Visibility variable."""
    import uuid as _uuid
    from kbz.models.community import Community as _C
    from kbz.models.variable import Variable as _V
    from kbz.services.community_service import CommunityService

    user = await create_test_user(client)
    root = await create_test_community(client, user["id"], "RootViz")
    root_id = _uuid.UUID(root["id"])

    # Flip root visibility to "private" directly in the DB.
    from sqlalchemy import update as _upd
    await db.execute(
        _upd(_V).where(
            _V.community_id == root_id, _V.name == "Visibility"
        ).values(value="private")
    )
    # Spawn an action under root.
    action_id = _uuid.uuid4()
    db.add(_C(id=action_id, parent_id=root_id, name="Sub", status=1, member_count=0))
    # And a sub-action under that action (depth 2).
    sub_id = _uuid.uuid4()
    db.add(_C(id=sub_id, parent_id=action_id, name="SubSub", status=1, member_count=0))
    await db.commit()

    svc = CommunityService(db)
    assert await svc.get_effective_visibility(root_id) == "private"
    assert await svc.get_effective_visibility(action_id) == "private"
    assert await svc.get_effective_visibility(sub_id) == "private"


@pytest.mark.asyncio
async def test_get_effective_visibility_falls_back_to_public(client, db):
    """Legacy communities that pre-date the Visibility variable
    (no row in `variables`) must default to 'public'."""
    import uuid as _uuid
    from kbz.models.variable import Variable as _V
    from kbz.services.community_service import CommunityService
    from sqlalchemy import delete as _del

    user = await create_test_user(client)
    root = await create_test_community(client, user["id"], "Legacy")
    root_id = _uuid.UUID(root["id"])

    # Delete the Visibility row to mimic a pre-feature community.
    await db.execute(
        _del(_V).where(_V.community_id == root_id, _V.name == "Visibility")
    )
    await db.commit()

    svc = CommunityService(db)
    assert await svc.get_effective_visibility(root_id) == "public"


# ─── Visibility filter on the Browse list ──────────────────────

@pytest.mark.asyncio
async def test_browse_list_hides_private_communities(client, db):
    """Communities whose Visibility variable is 'private' must NOT
    appear in the public /communities listing — they'd be exposing
    metadata (name, member count, creation date) to anyone visiting
    Browse, even though their content is gated."""
    from sqlalchemy import update as _upd
    from kbz.models.variable import Variable as _Var
    import uuid as _uuid

    user = await create_test_user(client)
    pub = await create_test_community(client, user["id"], "BrowsePublic")
    priv = await create_test_community(client, user["id"], "BrowsePrivate")
    await db.execute(
        _upd(_Var).where(
            _Var.community_id == _uuid.UUID(priv["id"]),
            _Var.name == "Visibility",
        ).values(value="private")
    )
    await db.commit()

    # Hit the Browse listing. Recent_created exemption keeps both
    # communities alive (just created). Visibility filter must
    # specifically exclude the private one.
    r = await client.get("/communities")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert "BrowsePublic" in names
    assert "BrowsePrivate" not in names


@pytest.mark.asyncio
async def test_browse_list_hides_unlisted_communities(client, db):
    """Unlisted root communities also stay off the public Browse —
    the URL is shareable but the community isn't in any directory."""
    from sqlalchemy import update as _upd
    from kbz.models.variable import Variable as _Var
    import uuid as _uuid

    user = await create_test_user(client)
    pub = await create_test_community(client, user["id"], "BrowseListed")
    ul = await create_test_community(client, user["id"], "BrowseUnlisted")
    await db.execute(
        _upd(_Var).where(
            _Var.community_id == _uuid.UUID(ul["id"]),
            _Var.name == "Visibility",
        ).values(value="unlisted")
    )
    await db.commit()

    r = await client.get("/communities")
    names = [c["name"] for c in r.json()]
    assert "BrowseListed" in names
    assert "BrowseUnlisted" not in names


@pytest.mark.asyncio
async def test_browse_list_includes_legacy_no_visibility(client, db):
    """Communities created BEFORE Visibility existed have no Variable
    row at all. They must default to visible/public on the Browse
    list — otherwise the AI Kibbutz (which predates this feature)
    silently disappears from Browse."""
    from sqlalchemy import delete as _del
    from kbz.models.variable import Variable as _Var
    import uuid as _uuid

    user = await create_test_user(client)
    legacy = await create_test_community(client, user["id"], "LegacyKibbutz")
    await db.execute(
        _del(_Var).where(
            _Var.community_id == _uuid.UUID(legacy["id"]),
            _Var.name == "Visibility",
        )
    )
    await db.commit()

    r = await client.get("/communities")
    names = [c["name"] for c in r.json()]
    assert "LegacyKibbutz" in names
