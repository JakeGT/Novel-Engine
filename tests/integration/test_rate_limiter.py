import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.rate_limit import PostgresRateLimiter
from backend.core.database import create_engine, create_session_factory

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def limiter(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresRateLimiter(create_session_factory(engine)), engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_limiter_allows_up_to_limit_then_blocks(limiter) -> None:
    rate_limiter, _engine = limiter

    assert await rate_limiter.allow("accept:user-1", limit=2, window_seconds=60) is True
    assert await rate_limiter.allow("accept:user-1", limit=2, window_seconds=60) is True
    assert await rate_limiter.allow("accept:user-1", limit=2, window_seconds=60) is False
    # A different key is unaffected.
    assert await rate_limiter.allow("accept:user-2", limit=2, window_seconds=60) is True


@pytest.mark.asyncio
async def test_limiter_prunes_hits_outside_window(limiter) -> None:
    rate_limiter, engine = limiter
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO rate_limit_hits (key, occurred_at) "
                "VALUES ('accept:user-3', now() - interval '120 seconds')"
            )
        )
        await session.commit()

    # The stale hit is outside the 60s window and must be pruned before counting.
    assert await rate_limiter.allow("accept:user-3", limit=1, window_seconds=60) is True
