import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.core.database import create_engine, create_session_factory, session_scope

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session_factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_commit_persists_and_rollback_discards(session_factory) -> None:
    async with session_scope(session_factory) as session:
        await session.execute(
            text("INSERT INTO infrastructure_metadata (key, value) VALUES ('committed', 'yes')")
        )

    async with session_scope(session_factory) as session:
        value = (
            await session.execute(
                text("SELECT value FROM infrastructure_metadata WHERE key = 'committed'")
            )
        ).scalar()
    assert value == "yes"

    with pytest.raises(RuntimeError):
        async with session_scope(session_factory) as session:
            await session.execute(
                text("INSERT INTO infrastructure_metadata (key, value) VALUES ('rolled', 'no')")
            )
            raise RuntimeError("boom")

    async with session_scope(session_factory) as session:
        value = (
            await session.execute(
                text("SELECT value FROM infrastructure_metadata WHERE key = 'rolled'")
            )
        ).scalar()
    assert value is None
