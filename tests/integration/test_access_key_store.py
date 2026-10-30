"""PostgreSQL store for access keys (QRY-003): digests only, use counts, deletion with scope."""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest
import pytest_asyncio

from backend.adapters.access_key_store import PostgresAccessKeyStore
from backend.core.database import create_engine, create_session_factory
from backend.core.security import hash_token
from backend.domain.access_keys import AccessKey, RevokedBecause, generate_key, key_hint
from backend.domain.identity import utcnow

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def store(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresAccessKeyStore(create_session_factory(engine)), disposable_database
    finally:
        await engine.dispose()


def _key(key_id: str, *, workspace: str = "ws1", project: str | None = "prj1") -> tuple:
    raw = generate_key()
    now = utcnow()
    return (
        AccessKey(
            id=key_id,
            workspace_id=workspace,
            user_id="u1",
            project_id=project,
            name="Claude",
            hint=key_hint(raw),
            token_hash=hash_token(raw),
            created_at=now,
            expires_at=now + timedelta(days=90),
        ),
        raw,
    )


@pytest.mark.asyncio
async def test_only_the_digest_is_written_and_a_key_is_found_by_it(store) -> None:
    keys, database = store
    key, raw = _key("key_1")
    await keys.create(key)

    with psycopg.connect(database.psycopg_dsn) as connection:
        row = connection.execute("SELECT * FROM access_keys WHERE id = 'key_1'").fetchone()
    assert raw not in repr(row)
    found = await keys.get_by_hash(hash_token(raw))
    assert found is not None and found.id == "key_1" and found.project_id == "prj1"
    assert await keys.get(workspace_id="ws2", key_id="key_1") is None
    assert [item.id for item in await keys.list_keys(workspace_id="ws1", user_id="u1")] == ["key_1"]


@pytest.mark.asyncio
async def test_revoking_once_touching_rarely_and_counting_use(store) -> None:
    keys, _database = store
    key, _raw = _key("key_1")
    await keys.create(key)
    at = utcnow()

    assert await keys.revoke(key_id="key_1", reason=RevokedBecause.HOLDER.value, at=at) is True
    assert await keys.revoke(key_id="key_1", reason=RevokedBecause.OWNER.value, at=at) is False
    stored = await keys.get(workspace_id="ws1", key_id="key_1")
    assert stored is not None and stored.revoked_reason is RevokedBecause.HOLDER

    await keys.touch(key_id="key_1", at=at)
    await keys.touch(key_id="key_1", at=at + timedelta(seconds=10))
    touched = await keys.get(workspace_id="ws1", key_id="key_1")
    assert touched is not None and touched.last_used_at == at

    day = at.date().isoformat()
    for refused in (False, True, False):
        await keys.record_use(
            key_id="key_1", workspace_id="ws1", day=day, tool="read_entry", refused=refused
        )
    uses = await keys.uses(key_id="key_1", since_day=day)
    assert [(use.tool, use.calls, use.refused) for use in uses] == [("read_entry", 3, 1)]


@pytest.mark.asyncio
async def test_keys_go_with_their_story_or_workspace(store) -> None:
    keys, _database = store
    for key_id, project in (("key_a", "prj1"), ("key_b", "prj2"), ("key_all", None)):
        await keys.create(_key(key_id, project=project)[0])
    await keys.create(_key("key_other", workspace="ws2")[0])

    assert await keys.delete_project("ws1", "prj1") == 1
    assert {key.id for key in await keys.list_keys(workspace_id="ws1")} == {"key_b", "key_all"}
    assert await keys.delete_workspace("ws1") == 2
    assert await keys.list_keys(workspace_id="ws1") == []
    assert [key.id for key in await keys.list_keys(workspace_id="ws2")] == ["key_other"]


@pytest.mark.asyncio
async def test_a_removed_member_s_working_keys_stop_together(store) -> None:
    keys, _database = store
    for key_id, workspace in (("key_a", "ws1"), ("key_b", "ws1"), ("key_c", "ws2")):
        await keys.create(_key(key_id, workspace=workspace)[0])
    other = _key("key_other")[0].model_copy(update={"user_id": "u2"})
    await keys.create(other)
    at = utcnow()
    await keys.revoke(key_id="key_b", reason=RevokedBecause.HOLDER.value, at=at)

    stopped = await keys.revoke_for_member(
        workspace_id="ws1", user_id="u1", reason=RevokedBecause.ACCESS_LOST.value, at=at
    )
    assert stopped == ["key_a"]
    gone = await keys.get(workspace_id="ws1", key_id="key_a")
    assert gone is not None and gone.revoked_reason is RevokedBecause.ACCESS_LOST
    # Already withdrawn keeps its reason; another workspace and another member are untouched.
    kept = await keys.get(workspace_id="ws1", key_id="key_b")
    assert kept is not None and kept.revoked_reason is RevokedBecause.HOLDER
    for workspace, key_id in (("ws2", "key_c"), ("ws1", "key_other")):
        untouched = await keys.get(workspace_id=workspace, key_id=key_id)
        assert untouched is not None and untouched.revoked_at is None
    assert (
        await keys.revoke_for_member(
            workspace_id="ws1", user_id="u1", reason=RevokedBecause.ACCESS_LOST.value, at=at
        )
        == []
    )
