import uuid
import pytest
from tests.conftest import create_test_user, create_test_community


async def _login_email(client, email: str) -> str:
    """Magic-link login. Returns user_id; leaves session cookie set."""
    r = await client.post("/auth/request-magic-link", json={"email": email})
    r = await client.get(r.json()["link"])
    return r.json()["user"]["user_id"]


async def _real_proposal(client, user) -> str:
    """Create a real proposal in a fresh community and return its id.
    Comments now require the entity to exist; tests must attach to a
    real proposal_id rather than a random UUID."""
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": f"comment-target-{uuid.uuid4()}",
    })
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_add_comment(client):
    user = await create_test_user(client)
    entity_id = await _real_proposal(client, user)

    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "Great proposal!",
    })
    assert resp.status_code == 201
    comment = resp.json()
    assert comment["comment_text"] == "Great proposal!"
    assert comment["score"] == 0


@pytest.mark.asyncio
async def test_get_comments(client):
    user = await create_test_user(client)
    entity_id = await _real_proposal(client, user)

    await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "First comment",
    })
    await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "Second comment",
    })

    resp = await client.get(f"/entities/proposal/{entity_id}/comments")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_nested_comments(client):
    """Default behavior includes the full tree (parent + replies) as
    a flat list — the client groups by parent_comment_id to render
    threading. Pre-fix this returned root only and the zoomed comment
    view never showed any replies."""
    user = await create_test_user(client)
    entity_id = await _real_proposal(client, user)

    # Parent comment
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "Parent",
    })
    parent_id = resp.json()["id"]

    # Reply
    await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "Reply to parent",
        "parent_comment_id": parent_id,
    })

    # Default: full tree, parent + reply both returned.
    resp = await client.get(f"/entities/proposal/{entity_id}/comments")
    rows = resp.json()
    assert len(rows) == 2
    texts = {r["comment_text"] for r in rows}
    assert texts == {"Parent", "Reply to parent"}
    # The reply carries parent_comment_id pointing back at the parent
    # so the client can rebuild the tree.
    reply = next(r for r in rows if r["comment_text"] == "Reply to parent")
    assert reply["parent_comment_id"] == parent_id

    # include_replies=false preserves the legacy compact-view behavior.
    resp = await client.get(
        f"/entities/proposal/{entity_id}/comments?include_replies=false"
    )
    assert len(resp.json()) == 1
    assert resp.json()[0]["comment_text"] == "Parent"


@pytest.mark.asyncio
async def test_comment_vote_is_per_user_and_toggle(client):
    """A user has at most one vote per comment. Pressing the same
    direction twice toggles it off; pressing the opposite flips it.
    Pre-fix the score endpoint blindly added the delta with no
    per-user dedupe, so a single user pressing up 20 times added 20
    points."""
    voter = await create_test_user(client, "voter")
    author = await create_test_user(client, "score-author")
    entity_id = await _real_proposal(client, author)

    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": author["id"],
        "comment_text": "Score me",
    })
    comment_id = resp.json()["id"]
    body = {"user_id": voter["id"]}

    # First upvote: score 0 → 1, my_value=+1.
    r = await client.post(
        f"/comments/{comment_id}/score", json={**body, "delta": 1},
    )
    assert r.json()["score"] == 1
    assert r.json()["my_value"] == 1
    assert r.json()["id"] == comment_id

    # Press up again: this is the bug repro. Pre-fix score went to
    # 2; post-fix the vote toggles off (score back to 0, no vote).
    r = await client.post(
        f"/comments/{comment_id}/score", json={**body, "delta": 1},
    )
    assert r.json()["score"] == 0
    assert r.json()["my_value"] is None

    # 20 more up clicks: score must oscillate 0 ↔ 1, never reach 20.
    for _ in range(20):
        r = await client.post(
            f"/comments/{comment_id}/score", json={**body, "delta": 1},
        )
    # 20 toggles after the previous "off" state ends back at "off"
    # (even number of clicks) — so my_value=None and score=0.
    assert r.json()["score"] == 0
    assert r.json()["my_value"] is None

    # Cast +1 once more, then flip to -1.
    r = await client.post(
        f"/comments/{comment_id}/score", json={**body, "delta": 1},
    )
    assert r.json()["score"] == 1
    assert r.json()["my_value"] == 1
    r = await client.post(
        f"/comments/{comment_id}/score", json={**body, "delta": -1},
    )
    # Flip: prior +1 cancels (-1) and -1 applies (-1) → net delta -2.
    assert r.json()["score"] == -1
    assert r.json()["my_value"] == -1


@pytest.mark.asyncio
async def test_comment_vote_independent_across_users(client):
    """Two different users each get their own vote on the same
    comment — score sums across them."""
    a = await create_test_user(client, "voter-a")
    b = await create_test_user(client, "voter-b")
    author = await create_test_user(client, "score-author2")
    entity_id = await _real_proposal(client, author)
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": author["id"], "comment_text": "two voters",
    })
    cid = resp.json()["id"]

    r = await client.post(
        f"/comments/{cid}/score", json={"user_id": a["id"], "delta": 1},
    )
    assert r.json()["score"] == 1
    r = await client.post(
        f"/comments/{cid}/score", json={"user_id": b["id"], "delta": 1},
    )
    assert r.json()["score"] == 2

    # Voter B flips to -1 → score = 0 (a's +1, b's -1).
    r = await client.post(
        f"/comments/{cid}/score", json={"user_id": b["id"], "delta": -1},
    )
    assert r.json()["score"] == 0


@pytest.mark.asyncio
async def test_comment_listing_carries_my_vote(client):
    """GET /entities/{kind}/{id}/comments stamps each row with the
    viewer's own vote — lets the dashboard highlight the up/down
    arrow already cast without per-comment lookups."""
    # Setup unauthenticated so session-spoof guards don't 403 us.
    author = await create_test_user(client, "list-author")
    entity_id = await _real_proposal(client, author)
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": author["id"], "comment_text": "list me",
    })
    cid = resp.json()["id"]
    voter_id = await _login_email(client, "list-voter@example.com")
    await client.post(
        f"/comments/{cid}/score", json={"user_id": voter_id, "delta": 1},
    )

    resp = await client.get(f"/entities/proposal/{entity_id}/comments")
    rows = resp.json()
    row = next(r for r in rows if r["id"] == cid)
    assert row["my_value"] == 1
    assert row["score"] == 1

    # Without a session: my_value must be None for every row.
    client.cookies.clear()
    resp = await client.get(f"/entities/proposal/{entity_id}/comments")
    rows = resp.json()
    assert all(r["my_value"] is None for r in rows)


@pytest.mark.asyncio
async def test_unknown_entity_type_rejected(client):
    """Only 'proposal' and 'community' are wired through the service +
    tkg ingestor; anything else silently created orphan rows. Reject
    at the router so API consumers get a clean 422."""
    user = await create_test_user(client)
    entity_id = str(uuid.uuid4())
    resp = await client.post(f"/entities/foobar/{entity_id}/comments", json={
        "user_id": user["id"], "comment_text": "orphan",
    })
    assert resp.status_code == 422
    resp = await client.get(f"/entities/foobar/{entity_id}/comments")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_oversized_comment_text_422s(client):
    """comment_text column is String(2000); without a schema cap the DB
    error surfaced as a 500. Enforce the 2000-char bound at the edge."""
    user = await create_test_user(client)
    entity_id = str(uuid.uuid4())
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"], "comment_text": "x" * 2001,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_empty_comment_text_422s(client):
    user = await create_test_user(client)
    entity_id = str(uuid.uuid4())
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"], "comment_text": "",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_score_missing_comment_is_404(client):
    """Voting on a comment that doesn't exist should 404."""
    voter = await create_test_user(client, "ghost-voter")
    missing = uuid.uuid4()
    resp = await client.post(
        f"/comments/{missing}/score",
        json={"user_id": voter["id"], "delta": 1},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_score_delta_must_be_single_step(client):
    """delta is clamped to [-1, 1] at the schema layer."""
    voter = await create_test_user(client, "clamp-voter")
    author = await create_test_user(client, "clamp-author")
    entity_id = await _real_proposal(client, author)
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": author["id"],
        "comment_text": "Clamp target",
    })
    cid = resp.json()["id"]
    resp = await client.post(
        f"/comments/{cid}/score",
        json={"user_id": voter["id"], "delta": 9999},
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"/comments/{cid}/score",
        json={"user_id": voter["id"], "delta": -42},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_score_session_spoof_blocked(client):
    """A logged-in attacker can't POST a vote with someone else's
    user_id. Without the session-binding check on the score endpoint,
    a single attacker could brigade by spoofing user_id per-call to
    bypass the per-user dedupe entirely."""
    victim = await create_test_user(client, "spoof-victim-vote")
    author = await create_test_user(client, "score-author3")
    entity_id = await _real_proposal(client, author)
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": author["id"], "comment_text": "spoof target",
    })
    cid = resp.json()["id"]

    # Attacker logs in then tries to vote AS victim.
    await _login_email(client, "spoof-attacker-vote@example.com")
    r = await client.post(
        f"/comments/{cid}/score",
        json={"user_id": victim["id"], "delta": 1},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reply_rejects_parent_on_different_entity(client):
    """A reply's parent_comment_id must live on the same entity —
    otherwise a reply can jump threads (e.g. claim to reply to comment
    on proposal A while posting on proposal B) and the tree view
    silently loses the thread."""
    user = await create_test_user(client)
    entity_a = await _real_proposal(client, user)
    entity_b = await _real_proposal(client, user)

    parent = await client.post(f"/entities/proposal/{entity_a}/comments", json={
        "user_id": user["id"],
        "comment_text": "Parent on A",
    })
    parent_id = parent.json()["id"]

    # Try to reply on entity B using a parent from entity A.
    bogus = await client.post(f"/entities/proposal/{entity_b}/comments", json={
        "user_id": user["id"],
        "comment_text": "cross-thread reply",
        "parent_comment_id": parent_id,
    })
    assert bogus.status_code == 400


@pytest.mark.asyncio
async def test_reply_rejects_missing_parent(client):
    """parent_comment_id pointing at nothing → 404, not silent orphan."""
    user = await create_test_user(client)
    entity_id = await _real_proposal(client, user)
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "reply to ghost",
        "parent_comment_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_comment_on_missing_proposal_404s(client):
    """POST /entities/proposal/<bogus-uuid>/comments must 404. Pre-fix
    the comment landed in the DB attached to a non-existent
    proposal_id — nobody got notified (recipient lookup hit None and
    silently bailed) and the row was functionally invisible except
    via direct GET against the bogus id. Same silent-no-op shape as
    the executor bugs we've been fixing throughout this cycle."""
    user = await create_test_user(client)
    bogus_id = str(uuid.uuid4())
    resp = await client.post(f"/entities/proposal/{bogus_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "ghost comment on a missing proposal",
    })
    assert resp.status_code == 404, resp.text
    assert "proposal" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_comment_on_missing_community_404s(client):
    """Same shape for the 'community' entity_type — POST against a
    bogus community_id must 404 instead of silently creating an
    orphan comment row."""
    user = await create_test_user(client)
    bogus_id = str(uuid.uuid4())
    resp = await client.post(f"/entities/community/{bogus_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "ghost comment on a missing community",
    })
    assert resp.status_code == 404, resp.text
    assert "community" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_top_level_community_comment_notifies_active_members(client):
    """Pre-fix a top-level comment on a community itself notified
    NOBODY — no proposal author, no parent-comment author. Now we
    fan out to every active member of the community (except the
    commenter)."""
    # Founder + a second active member.
    founder = await create_test_user(client, "ccmt-founder")
    other = await create_test_user(client, "ccmt-other")
    community = await create_test_community(client, founder["id"])
    # Land 'other' as a member.
    mid = (
        await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": other["id"], "proposal_type": "Membership",
            "proposal_text": "join", "val_uuid": other["id"],
        })
    ).json()["id"]
    await client.patch(f"/proposals/{mid}/submit")
    await client.post(f"/proposals/{mid}/support", json={"user_id": founder["id"]})
    for _ in range(2):
        await client.post(
            f"/communities/{community['id']}/pulses/support",
            json={"user_id": founder["id"]},
        )

    # Post a community-level comment as the founder. No session
    # cookie → agent passthrough.
    client.cookies.clear()
    r = await client.post(
        f"/entities/community/{community['id']}/comments",
        json={"user_id": founder["id"], "comment_text": "anyone here?"},
    )
    assert r.status_code == 201, r.text

    # 'other' should have a comment.posted notification.
    client.cookies.clear()
    r = await client.post("/auth/request-magic-link", json={"email": "other-notif@example.com"})
    # Magic link creates the user — but we want OUR existing 'other'.
    # Use the agent-style direct session by logging in as the email
    # we know matches the user.
    # Simpler: switch user via direct cookie-clearing then GET with
    # an unauthenticated check against the notifications by user_id.
    # Easiest: use the api_client pattern from agent tests — but
    # here we just check the DB state directly via a separate call.
    from kbz.services.notification_service import NotificationService
    # Can't easily reach NotificationService from the test client
    # without raw SQL. Use the GET endpoint for the OTHER user via
    # a magic-link login as them.
    client.cookies.clear()
    # The 'other' user was created via /users (no email). Magic-link
    # will get-or-create a NEW user keyed on the email — different id.
    # So check via raw DB.
    from sqlalchemy import select as _sel
    from kbz.models.notification import Notification
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from kbz.config import settings
    from sqlalchemy.ext.asyncio import create_async_engine
    eng = create_async_engine(settings.test_database_url)
    sf = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with sf() as s:
        rows = (await s.execute(
            _sel(Notification).where(Notification.user_id == uuid.UUID(other["id"]))
        )).scalars().all()
    await eng.dispose()
    matching = [
        n for n in rows
        if n.kind == "comment.posted"
        and (n.payload_json or {}).get("entity_type") == "community"
    ]
    assert len(matching) == 1, (
        f"expected exactly one community comment.posted notification "
        f"for the other member; got: {[(n.kind, n.payload_json) for n in rows]}"
    )

    # Founder (the commenter) must NOT get a self-notify.
    async with sf() as s:
        founder_rows = (await s.execute(
            _sel(Notification).where(Notification.user_id == uuid.UUID(founder["id"]))
        )).scalars().all()
    await eng.dispose()
    founder_self_match = [
        n for n in founder_rows
        if n.kind == "comment.posted"
        and (n.payload_json or {}).get("entity_type") == "community"
    ]
    assert founder_self_match == [], (
        "founder should not get a self-notify on their own community comment"
    )
