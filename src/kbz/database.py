from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kbz.config import settings

# Pool tuned for HN-launch traffic. The SQLAlchemy default of
# pool_size=5 + max_overflow=10 (15 connections total) saturates at
# ~15 concurrent DB-bound coroutines — under HN traffic hitting the
# public read endpoints that's instant queue-up. With 40 connections
# available + pre_ping + recycle, a 4-worker uvicorn process can
# sustain ~200 RPS against /highlights and /artifact/<id>/share
# without queueing.
#
# pool_pre_ping=True   : send a cheap SELECT 1 before each checkout
#                        so we catch the dead-after-network-blip
#                        case (Hetzner sometimes drops idle TCP).
# pool_recycle=300     : force connection recycling every 5 min so
#                        we don't accumulate stale connections that
#                        Postgres has half-closed.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=20,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=300,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
