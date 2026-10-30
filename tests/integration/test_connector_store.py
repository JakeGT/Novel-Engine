"""PostgreSQL store for connected computers and their request queue (SCN-002A, ADR-025)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import psycopg
import pytest
import pytest_asyncio

from backend.adapters.connector_store import PostgresConnectorStore
from backend.core.database import create_engine, create_session_factory
from backend.core.security import hash_token
from backend.domain import connector as c
from backend.domain.identity import utcnow

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def store(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresConnectorStore(create_session_factory(engine)), disposable_database
    finally:
        await engine.dispose()


def _code(user_code: str = "BCDFGHJK") -> tuple[c.PendingCode, str]:
    device_code = c.generate_device_code()
    now = utcnow()
    return (
        c.PendingCode(
            device_code_hash=hash_token(device_code),
            user_code=user_code,
            device_name="Laptop",
            connector_version="1.0.0",
            status=c.CodeStatus.PENDING,
            created_at=now,
            expires_at=now + c.CODE_LIFETIME,
        ),
        device_code,
    )


def _device(device_id: str, credential: str, workspace: str = "ws1") -> c.Device:
    now = utcnow()
    return c.Device(
        id=device_id,
        workspace_id=workspace,
        name="Laptop",
        registered_by="u1",
        status=c.DeviceStatus.ACTIVE,
        credential_hash=hash_token(credential),
        credential_issued_at=now,
        created_at=now,
        connector_version="1.0.0",
        last_seen_at=now,
    )


async def _connected(connectors, device_id="dev_1", workspace="ws1", user_code="BCDFGHJK"):
    code, _ = _code(user_code)
    assert await connectors.add_code(code)
    await connectors.decide_code(
        user_code=code.user_code, approve=True, workspace_id=workspace, decided_by="u1", at=utcnow()
    )
    credential = c.generate_credential()
    assert await connectors.issue_code(
        device_code_hash=code.device_code_hash, device=_device(device_id, credential, workspace)
    )
    return credential


@pytest.mark.asyncio
async def test_a_code_is_confirmed_once_and_used_once_and_only_digests_are_written(store):
    connectors, database = store
    code, device_code = _code()
    assert await connectors.add_code(code)
    # The short code is unique while it is kept.
    duplicate, _ = _code()
    assert await connectors.add_code(duplicate) is False

    found = await connectors.code_by_device_hash(hash_token(device_code))
    assert found is not None and found.status is c.CodeStatus.PENDING
    decided = await connectors.decide_code(
        user_code="BCDFGHJK", approve=True, workspace_id="ws1", decided_by="u1", at=utcnow()
    )
    assert decided is not None and decided.status is c.CodeStatus.APPROVED
    again = await connectors.decide_code(
        user_code="BCDFGHJK", approve=False, workspace_id="ws2", decided_by="u2", at=utcnow()
    )
    assert again is None

    credential = c.generate_credential()
    device = _device("dev_1", credential)
    assert await connectors.issue_code(device_code_hash=code.device_code_hash, device=device)
    assert (
        await connectors.issue_code(device_code_hash=code.device_code_hash, device=device) is False
    )

    with psycopg.connect(database.psycopg_dsn) as connection:
        rows = repr(connection.execute("SELECT * FROM connector_codes").fetchall())
        rows += repr(connection.execute("SELECT * FROM connector_devices").fetchall())
    assert device_code not in rows and credential not in rows
    assert (await connectors.device_by_credential(hash_token(credential))).id == "dev_1"


@pytest.mark.asyncio
async def test_an_expired_code_cannot_be_confirmed(store):
    connectors, _ = store
    code, _ = _code("ZZZZZZZZ")
    code.expires_at = utcnow() - timedelta(seconds=1)
    assert await connectors.add_code(code)
    decided = await connectors.decide_code(
        user_code="ZZZZZZZZ", approve=True, workspace_id="ws1", decided_by="u1", at=utcnow()
    )
    assert decided is None


@pytest.mark.asyncio
async def test_rotation_keeps_the_old_credential_findable_and_revoking_clears_it(store):
    connectors, _ = store
    old = await _connected(connectors)
    new = c.generate_credential()
    device = await connectors.device("dev_1")
    assert await connectors.rotate(
        device_id="dev_1",
        expected_hash=device.credential_hash,
        new_hash=hash_token(new),
        previous_hash=hash_token(old),
        at=utcnow(),
        grace_until=utcnow() + c.ROTATION_GRACE,
    )
    # A second rotation from the same starting point loses the race.
    assert not await connectors.rotate(
        device_id="dev_1",
        expected_hash=device.credential_hash,
        new_hash=hash_token(c.generate_credential()),
        previous_hash=hash_token(old),
        at=utcnow(),
        grace_until=utcnow(),
    )
    assert (await connectors.device_by_credential(hash_token(old))).id == "dev_1"
    assert (await connectors.device_by_credential(hash_token(new))).id == "dev_1"

    await connectors.seen(
        device_id="dev_1",
        at=utcnow(),
        version="1.0.1",
        capabilities={"server": "ollama", "models": [{"name": "m", "context_tokens": 4096}]},
    )
    seen = await connectors.device("dev_1")
    assert seen.connector_version == "1.0.1"
    assert seen.capabilities.models == (c.LocalModel("m", 4096),)

    await connectors.set_in_use(workspace_id="ws1", device_id="dev_1")
    assert (await connectors.in_use("ws1")).id == "dev_1"
    revoked = await connectors.revoke(
        workspace_id="ws1", device_id="dev_1", reason="owner", by="u1", at=utcnow()
    )
    assert revoked is not None and revoked.status is c.DeviceStatus.REVOKED
    assert revoked.in_use is False and revoked.previous_hash is None
    assert await connectors.in_use("ws1") is None
    assert (
        await connectors.revoke(
            workspace_id="ws1", device_id="dev_1", reason="owner", by="u1", at=utcnow()
        )
        is None
    )


@pytest.mark.asyncio
async def test_requests_are_taken_once_answered_by_their_device_and_their_words_cleared(store):
    connectors, database = store
    await _connected(connectors, "dev_1", user_code="BBBBBBBB")
    await _connected(connectors, "dev_2", user_code="CCCCCCCC")
    words = "Wren crossed the yard of the mill."
    request = c.ChannelRequest(
        id="creq_1",
        workspace_id="ws1",
        device_id="dev_1",
        actor_user_id="u1",
        kind="POST /chat/completions",
        status=c.RequestStatus.WAITING,
        created_at=utcnow(),
        payload={"method": "POST", "path": "/chat/completions", "body": {"messages": [words]}},
        model="m",
    )
    await connectors.enqueue(request)
    since = utcnow() - timedelta(minutes=1)
    assert await connectors.take_next(device_id="dev_2", at=utcnow(), since=since) is None
    # Two polls at once never take the same request.
    first, second = await asyncio.gather(
        connectors.take_next(device_id="dev_1", at=utcnow(), since=since),
        connectors.take_next(device_id="dev_1", at=utcnow(), since=since),
    )
    assert [r is not None for r in (first, second)].count(True) == 1
    taken = first or second
    assert taken.payload["body"]["messages"] == [words]

    answer = {
        "status": "done",
        "result": {"choices": []},
        "result_status": 200,
        "failure": None,
        "model": "m",
        "prompt_tokens": 3,
        "completion_tokens": 4,
        "at": utcnow(),
    }
    assert not await connectors.answer(device_id="dev_2", request_id="creq_1", **answer)
    assert await connectors.answer(device_id="dev_1", request_id="creq_1", **answer)
    done = await connectors.request("creq_1")
    assert done.status is c.RequestStatus.DONE and done.payload is None
    await connectors.close(request_id="creq_1", failure="gave_up", at=utcnow())
    closed = await connectors.request("creq_1")
    assert closed.status is c.RequestStatus.DONE and closed.result is None
    assert (closed.prompt_tokens, closed.completion_tokens) == (3, 4)
    with psycopg.connect(database.psycopg_dsn) as connection:
        rows = repr(connection.execute("SELECT * FROM connector_requests").fetchall())
    assert words not in rows

    # An open request is expired, and its words cleared, when its waiter gives up.
    await connectors.enqueue(
        c.ChannelRequest(**{**request.__dict__, "id": "creq_2", "created_at": utcnow()})
    )
    await connectors.close(request_id="creq_2", failure="not_taken", at=utcnow())
    expired = await connectors.request("creq_2")
    assert expired.status is c.RequestStatus.EXPIRED and expired.payload is None
    assert expired.failure == "not_taken"

    # Disconnecting fails what is still waiting for it.
    await connectors.enqueue(
        c.ChannelRequest(**{**request.__dict__, "id": "creq_3", "created_at": utcnow()})
    )
    await connectors.revoke(
        workspace_id="ws1", device_id="dev_1", reason="owner", by=None, at=utcnow()
    )
    failed = await connectors.request("creq_3")
    assert failed.status is c.RequestStatus.FAILED and failed.payload is None


@pytest.mark.asyncio
async def test_deleting_a_workspace_removes_its_computers_codes_and_requests(store):
    connectors, _ = store
    await _connected(connectors, "dev_1", "ws1", "BBBBBBBB")
    await _connected(connectors, "dev_9", "ws9", "DDDDDDDD")
    assert await connectors.delete_workspace("ws1") >= 2
    assert await connectors.devices("ws1") == []
    assert [d.id for d in await connectors.devices("ws9")] == ["dev_9"]


def _request(request_id: str, created_at, *, device="dev_1", project=None) -> c.ChannelRequest:
    return c.ChannelRequest(
        id=request_id,
        workspace_id="ws1",
        project_id=project,
        device_id=device,
        actor_user_id="u1",
        kind="POST /chat/completions",
        status=c.RequestStatus.WAITING,
        created_at=created_at,
        payload={"method": "POST", "path": "/chat/completions", "body": {"m": "Wren ran."}},
    )


@pytest.mark.asyncio
async def test_only_a_recent_request_for_a_connected_computer_is_handed_out(store):
    connectors, _ = store
    await _connected(connectors, "dev_1", user_code="BBBBBBBB")
    now = utcnow()
    await connectors.enqueue(_request("creq_old", now - timedelta(minutes=5)))
    since = now - timedelta(seconds=c.PICKUP_SECONDS)
    assert await connectors.take_next(device_id="dev_1", at=now, since=since) is None
    await connectors.enqueue(_request("creq_new", now))
    await connectors.revoke(workspace_id="ws1", device_id="dev_1", reason="owner", by=None, at=now)
    # Revoking failed it; one written after (a race) is still not handed to a revoked computer.
    await connectors.enqueue(_request("creq_late", utcnow()))
    assert await connectors.take_next(device_id="dev_1", at=utcnow(), since=since) is None


@pytest.mark.asyncio
async def test_the_sweep_clears_words_a_crash_left_behind(store):
    connectors, database = store
    await _connected(connectors, "dev_1", user_code="BBBBBBBB")
    now = utcnow()
    await connectors.enqueue(_request("creq_stuck", now - timedelta(hours=1)))
    await connectors.enqueue(_request("creq_unread", now - timedelta(minutes=20)))
    since = now - timedelta(hours=2)
    taken = await connectors.take_next(device_id="dev_1", at=now, since=since)
    assert taken.id == "creq_stuck"
    taken = await connectors.take_next(device_id="dev_1", at=now, since=since)
    await connectors.answer(
        device_id="dev_1",
        request_id="creq_unread",
        status="done",
        result={"choices": ["The tide turned."]},
        result_status=200,
        failure=None,
        model="m",
        prompt_tokens=1,
        completion_tokens=1,
        at=now - timedelta(minutes=10),
    )
    await connectors.enqueue(_request("creq_fresh", now))
    cleared = await connectors.sweep(
        at=now, open_before=now - timedelta(minutes=30), read_before=now - timedelta(minutes=5)
    )
    assert cleared == 2
    stuck = await connectors.request("creq_stuck")
    assert stuck.status is c.RequestStatus.EXPIRED and stuck.failure == "abandoned"
    assert stuck.payload is None
    unread = await connectors.request("creq_unread")
    assert unread.status is c.RequestStatus.DONE and unread.result is None
    assert (await connectors.request("creq_fresh")).payload is not None
    with psycopg.connect(database.psycopg_dsn) as connection:
        rows = repr(
            connection.execute(
                "SELECT * FROM connector_requests WHERE id <> 'creq_fresh'"
            ).fetchall()
        )
    assert "Wren ran." not in rows and "The tide turned." not in rows


@pytest.mark.asyncio
async def test_deleting_a_story_removes_its_requests(store):
    connectors, _ = store
    await _connected(connectors, "dev_1", user_code="BBBBBBBB")
    await connectors.enqueue(_request("creq_story", utcnow(), project="novel"))
    await connectors.enqueue(_request("creq_other", utcnow(), project="other"))
    assert await connectors.delete_project("ws1", "novel") == 1
    assert await connectors.request("creq_story") is None
    assert (await connectors.request("creq_other")).project_id == "other"


@pytest.mark.asyncio
async def test_a_rotation_from_the_replaced_credential_waits_on_the_current_being_unused(store):
    connectors, _ = store
    first = await _connected(connectors)
    device = await connectors.device("dev_1")
    second = c.generate_credential()
    assert await connectors.rotate(
        device_id="dev_1",
        expected_hash=device.credential_hash,
        new_hash=hash_token(second),
        previous_hash=hash_token(first),
        at=utcnow(),
        grace_until=utcnow() + c.ROTATION_GRACE,
    )
    await connectors.credential_used(
        device_id="dev_1", credential_hash=hash_token(second), at=utcnow()
    )
    used = await connectors.device("dev_1")
    assert used.credential_last_used_at is not None
    assert not await connectors.rotate(
        device_id="dev_1",
        expected_hash=hash_token(second),
        new_hash=hash_token(c.generate_credential()),
        previous_hash=hash_token(second),
        at=utcnow(),
        grace_until=utcnow() + c.ROTATION_GRACE,
        only_if_unused=True,
    )
    assert await connectors.rotate(
        device_id="dev_1",
        expected_hash=hash_token(second),
        new_hash=hash_token(c.generate_credential()),
        previous_hash=hash_token(second),
        at=utcnow(),
        grace_until=utcnow() + c.ROTATION_GRACE,
    )
    assert (await connectors.device("dev_1")).credential_last_used_at is None
