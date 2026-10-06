import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.identity_store import PostgresIdentityStore, PostgresSessionStore
from backend.core.database import create_engine, create_session_factory
from backend.devtools.retention import purge_expired
from backend.domain.identity import IdentityClaims

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield create_session_factory(engine), disposable_database
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_purge_expired_sessions(factory) -> None:
    session_factory, database = factory
    user = await PostgresIdentityStore(session_factory).get_or_create_user(
        IdentityClaims(issuer="local", subject="u1")
    )
    store = PostgresSessionStore(session_factory)
    session = await store.create(user_id=user.id, ttl_seconds=3600, csrf_token="csrf")

    async with session_factory() as db_session:
        await db_session.execute(
            text("UPDATE sessions SET expires_at = now() - interval '1 day' WHERE id_hash = :h"),
            {"h": hashlib.sha256(session.id.encode()).hexdigest()},
        )
        await db_session.commit()

    counts = purge_expired(database.psycopg_dsn)
    assert counts["sessions"] == 1
    assert await store.get(session.id) is None
    assert counts["login_attempts"] == 0
    assert counts["invitations"] == 0


def test_purge_tolerates_unmigrated_database(disposable_database) -> None:
    unmigrated = disposable_database.settings(POSTGRES_DB="postgres")
    counts = purge_expired(unmigrated.postgres_dsn_psycopg)
    assert set(counts) == {"sessions", "login_attempts", "invitations", "rate_limit_hits"}
    assert all(isinstance(value, int) for value in counts.values())
