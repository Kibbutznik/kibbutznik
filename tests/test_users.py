import pytest
from tests.conftest import create_test_user


@pytest.mark.asyncio
async def test_create_user(client):
    user = await create_test_user(client, "alice")
    assert user["user_name"] == "alice"
    assert "id" in user
    assert "password_hash" not in user  # Should not leak


@pytest.mark.asyncio
async def test_get_user(client):
    user = await create_test_user(client, "bob")
    resp = await client.get(f"/users/{user['id']}")
    assert resp.status_code == 200
    assert resp.json()["user_name"] == "bob"


@pytest.mark.asyncio
async def test_get_user_not_found(client):
    resp = await client.get("/users/00000000-0000-0000-0000-000000000001")
    assert resp.status_code == 404
