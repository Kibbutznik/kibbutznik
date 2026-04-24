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
