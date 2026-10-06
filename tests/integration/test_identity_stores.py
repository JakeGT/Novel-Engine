import pytest
import pytest_asyncio

from backend.adapters.identity_store import (
    PostgresIdentityStore,
    PostgresLoginAttemptStore,
    PostgresSessionStore,
)
from backend.core.database import create_engine, create_session_factory
from backend.domain.identity import IdentityClaims

pytestmark = pytest.mark.integration

ISSUER = "https://idp.test/application/o/novel-engine/"


@pytest_asyncio.fixture
async def session_factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _make_user(session_factory, subject: str = "subject-1") -> str:
    store = PostgresIdentityStore(session_factory)
    user = await store.get_or_create_user(IdentityClaims(issuer=ISSUER, subject=subject))
    return user.id


@pytest.mark.asyncio
async def test_identity_store_links_by_issuer_subject(session_factory) -> None:
    store = PostgresIdentityStore(session_factory)
    claims = IdentityClaims(
        issuer=ISSUER, subject="subject-1", email="a@example.com", display_name="A"
    )

    user1 = await store.get_or_create_user(claims)
    user2 = await store.get_or_create_user(claims)
    assert user1.id == user2.id

    changed = claims.model_copy(update={"email": "b@example.com"})
    user3 = await store.get_or_create_user(changed)
    assert user3.id == user1.id
    assert (await store.get_user(user1.id)).email == "b@example.com"

    other = await store.get_or_create_user(IdentityClaims(issuer=ISSUER, subject="subject-2"))
    assert other.id != user1.id

    found = await store.get_user_for_identity(ISSUER, "subject-1")
    assert found is not None and found.id == user1.id
    assert await store.get_user_for_identity(ISSUER, "missing") is None


@pytest.mark.asyncio
async def test_session_store_roundtrip_and_revocation(session_factory) -> None:
    user_id = await _make_user(session_factory, "session-user")
    store = PostgresSessionStore(session_factory)

    session = await store.create(user_id=user_id, ttl_seconds=3600, csrf_token="csrf")
    fetched = await store.get(session.id)
    assert fetched is not None and fetched.user_id == user_id

    assert await store.revoke(session.id) is True
    assert await store.get(session.id) is None
    assert await store.revoke(session.id) is False


@pytest.mark.asyncio
async def test_session_store_expires(session_factory) -> None:
    user_id = await _make_user(session_factory, "expiring-user")
    store = PostgresSessionStore(session_factory)
    session = await store.create(user_id=user_id, ttl_seconds=-1, csrf_token="csrf")
    assert await store.get(session.id) is None


@pytest.mark.asyncio
async def test_revoke_all_for_user(session_factory) -> None:
    user_x = await _make_user(session_factory, "user-x")
    user_y = await _make_user(session_factory, "user-y")
    store = PostgresSessionStore(session_factory)
    await store.create(user_id=user_x, ttl_seconds=3600, csrf_token="c1")
    await store.create(user_id=user_x, ttl_seconds=3600, csrf_token="c2")
    await store.create(user_id=user_y, ttl_seconds=3600, csrf_token="c3")

    assert await store.revoke_all_for_user(user_x) == 2
    assert await store.revoke_all_for_user(user_x) == 0


@pytest.mark.asyncio
async def test_login_attempt_is_consumed_once(session_factory) -> None:
    store = PostgresLoginAttemptStore(session_factory)
    await store.create(
        state="s1", nonce="n", code_verifier="v", redirect_uri="https://x/cb", ttl_seconds=600
    )
    first = await store.consume("s1")
    assert first is not None and first.nonce == "n"
    assert await store.consume("s1") is None


@pytest.mark.asyncio
async def test_login_attempt_expires(session_factory) -> None:
    store = PostgresLoginAttemptStore(session_factory)
    await store.create(
        state="s2", nonce="n", code_verifier="v", redirect_uri="https://x/cb", ttl_seconds=-1
    )
    assert await store.consume("s2") is None


@pytest.mark.asyncio
async def test_unlink_identity_removes_link_but_keeps_user(session_factory) -> None:
    store = PostgresIdentityStore(session_factory)
    user_id = await _make_user(session_factory, "unlink-subject")
    assert await store.get_user_for_identity(ISSUER, "unlink-subject") is not None

    assert await store.unlink_identity(ISSUER, "unlink-subject") is True
    assert await store.get_user_for_identity(ISSUER, "unlink-subject") is None
    assert await store.get_user(user_id) is not None
    assert await store.unlink_identity(ISSUER, "unlink-subject") is False
