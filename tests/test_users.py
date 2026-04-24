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


@pytest.mark.asyncio
async def test_create_user_rejects_short_name(client):
    """PATCH /users/me enforces user_name 3..255; POST /users was
    unbounded, so users could sign up with a 1- or 2-char name that
    /me later refuses to let them change to anything shorter (and that
    wouldn't survive a rename round-trip either)."""
    resp = await client.post("/users", json={"user_name": "a", "password": "pw"})
    assert resp.status_code == 422
    resp = await client.post("/users", json={"user_name": "ab", "password": "pw"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_user_rejects_empty_password(client):
    resp = await client.post("/users", json={"user_name": "someone", "password": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_user_rejects_oversized_fields(client):
    resp = await client.post("/users", json={
        "user_name": "x" * 300, "password": "pw",
    })
    assert resp.status_code == 422
    resp = await client.post("/users", json={
        "user_name": "okname", "password": "pw", "about": "x" * 5000,
    })
    assert resp.status_code == 422
