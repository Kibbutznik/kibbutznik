import uuid
import pytest
from tests.conftest import create_test_user


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
async def test_comment_score(client):
    user = await create_test_user(client)
    entity_id = str(uuid.uuid4())

    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "Score me",
    })
    comment_id = resp.json()["id"]

    # Upvote — response now carries the NEW score so the client can
    # update its UI without re-fetching the whole proposal modal.
    r = await client.post(f"/comments/{comment_id}/score", json={"delta": 1})
    assert r.json()["score"] == 1
    assert r.json()["id"] == comment_id
    r = await client.post(f"/comments/{comment_id}/score", json={"delta": 1})
    assert r.json()["score"] == 2

    # Downvote
    r = await client.post(f"/comments/{comment_id}/score", json={"delta": -1})
    assert r.json()["score"] == 1

    # Net score should be +1 (and the GET reflects it too).
    resp = await client.get(f"/entities/proposal/{entity_id}/comments")
    assert resp.json()[0]["score"] == 1


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
    """Scoring a comment that doesn't exist should 404, not silently
    return 200. Previously the UPDATE affected zero rows and the router
    returned {"status": "updated"} anyway."""
    missing = uuid.uuid4()
    resp = await client.post(f"/comments/{missing}/score", json={"delta": 1})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_score_delta_must_be_single_step(client):
    """delta is clamped to [-1, 1] so a single POST can't pump a
    comment's score by an arbitrary amount."""
    user = await create_test_user(client)
    entity_id = str(uuid.uuid4())
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "Clamp target",
    })
    cid = resp.json()["id"]
    resp = await client.post(f"/comments/{cid}/score", json={"delta": 9999})
    assert resp.status_code == 422
    resp = await client.post(f"/comments/{cid}/score", json={"delta": -42})
    assert resp.status_code == 422


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
