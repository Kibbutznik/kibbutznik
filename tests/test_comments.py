import uuid
import pytest
from tests.conftest import create_test_user


async def _login_email(client, email: str) -> str:
    """Magic-link login. Returns user_id; leaves session cookie set."""
    r = await client.post("/auth/request-magic-link", json={"email": email})
    r = await client.get(r.json()["link"])
    return r.json()["user"]["user_id"]


@pytest.mark.asyncio
async def test_add_comment(client):
    user = await create_test_user(client)
    entity_id = str(uuid.uuid4())

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
    entity_id = str(uuid.uuid4())

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
    entity_id = str(uuid.uuid4())

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
    entity_id = str(uuid.uuid4())

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
    entity_id = str(uuid.uuid4())
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
    entity_id = str(uuid.uuid4())
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
    entity_id = str(uuid.uuid4())
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
    entity_id = str(uuid.uuid4())
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
    entity_a = str(uuid.uuid4())
    entity_b = str(uuid.uuid4())

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
    entity_id = str(uuid.uuid4())
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "reply to ghost",
        "parent_comment_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 404
