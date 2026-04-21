import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kbz.config import settings
from kbz.database import get_db
from kbz.main import app
from kbz.models import Base
from kbz.services.rate_limit import magic_link_limiter

TEST_DB_URL = settings.test_database_url


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate-limit buckets are a process-wide singleton. If we don't wipe
    between tests, the per-IP bucket (everything comes from 127.0.0.1
    under httpx's ASGITransport) fills up and later tests start getting
    429s. Wipe before each test."""
    magic_link_limiter._buckets.clear()
    yield


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# Helper factories
async def create_test_user(client: AsyncClient, name: str = None) -> dict:
    name = name or f"user_{uuid.uuid4().hex[:8]}"
    resp = await client.post("/users", json={"user_name": name, "password": "test123"})
    assert resp.status_code == 201
    return resp.json()


async def create_test_community(client: AsyncClient, user_id: str, name: str = "Test Community") -> dict:
    resp = await client.post("/communities", json={
        "name": name,
        "founder_user_id": user_id,
    })
    assert resp.status_code == 201
    return resp.json()
