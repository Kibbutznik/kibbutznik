import uuid
import pytest
from tests.conftest import create_test_user, create_test_community


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

    # Top-level comments should only show parent
    resp = await client.get(f"/entities/proposal/{entity_id}/comments")
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

    # Upvote
    await client.post(f"/comments/{comment_id}/score", json={"delta": 1})
    await client.post(f"/comments/{comment_id}/score", json={"delta": 1})

    # Downvote
    await client.post(f"/comments/{comment_id}/score", json={"delta": -1})

    # Net score should be +1
    resp = await client.get(f"/entities/proposal/{entity_id}/comments")
    assert resp.json()[0]["score"] == 1


@pytest.mark.asyncio
async def test_replies_are_listable_via_replies_endpoint(client):
    """Replies (parent_comment_id set) used to be effectively
    invisible — the top-level comments listing filters them out and
    no other endpoint exposed them. GET /comments/{id}/replies
    returns the children of a parent so a threaded UI can render
    the full tree."""
    user = await create_test_user(client)
    entity_id = str(uuid.uuid4())

    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "Parent",
    })
    parent_id = resp.json()["id"]

    await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "First reply",
        "parent_comment_id": parent_id,
    })
    await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "Second reply",
        "parent_comment_id": parent_id,
    })

    resp = await client.get(f"/comments/{parent_id}/replies")
    assert resp.status_code == 200
    bodies = sorted(r["comment_text"] for r in resp.json())
    assert bodies == ["First reply", "Second reply"]


@pytest.mark.asyncio
async def test_score_endpoint_returns_new_score(client):
    """Scoring a comment used to return `{status: updated}`, which
    forced clients to refetch the whole proposal to see the new
    count. The endpoint now returns the new score so the UI can
    update in-place."""
    user = await create_test_user(client)
    entity_id = str(uuid.uuid4())
    resp = await client.post(f"/entities/proposal/{entity_id}/comments", json={
        "user_id": user["id"],
        "comment_text": "score-shape",
    })
    comment_id = resp.json()["id"]

    resp = await client.post(f"/comments/{comment_id}/score", json={"delta": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == comment_id
    assert body["score"] == 1

    resp = await client.post(f"/comments/{comment_id}/score", json={"delta": -2})
    assert resp.json()["score"] == -1


@pytest.mark.asyncio
async def test_score_404_on_unknown_comment(client):
    """POST /comments/{bogus}/score used to silently succeed because
    UPDATE on a missing row matches zero rows — must 404 instead so
    a typo is visible."""
    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await client.post(f"/comments/{bogus}/score", json={"delta": 1})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_edit_artifact_top_level_comment_must_quote(client):
    """Top-level comments on an EditArtifact proposal must literally
    quote a 5-word run from the proposal text — anti-hallucination
    guard for agent commentary."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    # Drive this through the actual proposal flow so the row really
    # exists with proposal_type=EditArtifact.
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Land an artifact via AddStatement so we can target its id with
    # an EditArtifact. Easier: just craft a proposal directly with
    # type EditArtifact — the executor won't run since this isn't
    # accepted, but ProposalService.create will let it through with
    # a val_uuid. Use a real artifact via create_root_container's
    # implicit primordial.
    # Simpler: file a plain AddStatement proposal but spoof the
    # underlying row's type via the API isn't possible. Instead,
    # we use the artifact_service in-process to make a real artifact
    # then file an EditArtifact proposal targeting it.
    from kbz.database import get_db as _get_db  # noqa: F401
    # Easier path: hit /proposals with proposal_type='EditArtifact'.
    # The create endpoint accepts it as long as the user is a member.
    proposal_text = "alpha bravo charlie delta echo foxtrot golf"
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "EditArtifact",
        "proposal_text": proposal_text,
        "val_uuid": str(uuid.uuid4()),
    })
    assert resp.status_code == 201, resp.text
    proposal_id = resp.json()["id"]

    # Top-level comment without a literal quote → 422.
    resp = await client.post(
        f"/entities/proposal/{proposal_id}/comments",
        json={"user_id": user["id"], "comment_text": "this is great"},
    )
    assert resp.status_code == 422

    # Top-level comment WITH a literal quote → 201.
    resp = await client.post(
        f"/entities/proposal/{proposal_id}/comments",
        json={
            "user_id": user["id"],
            "comment_text": "I agree with 'bravo charlie delta echo foxtrot' here.",
        },
    )
    assert resp.status_code == 201
    parent_id = resp.json()["id"]

    # REPLY to the parent — exempt from the quote requirement.
    # Replies converse with another comment, not the proposal itself.
    resp = await client.post(
        f"/entities/proposal/{proposal_id}/comments",
        json={
            "user_id": user["id"],
            "comment_text": "I disagree",
            "parent_comment_id": parent_id,
        },
    )
    assert resp.status_code == 201, resp.text
