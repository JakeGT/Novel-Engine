"""The connector channel's safety rules, at the service, on a clock the test moves (SCN-002A).

Words a crash left in the queue, a copied credential, a demoted member's computer, polls
held open at once, and answers too large to keep (ADR-025).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest

from backend.core.security import hash_token
from backend.domain import connector as c
from backend.domain.errors import ForbiddenError, RateLimitedError, UnauthorizedError
from backend.domain.identity import AccessScope, AuthMode, Role, utcnow
from backend.services.connector_service import AnswerTooLarge, ConnectorService
from backend.services.tenancy_service import TenancyService
from tests.doubles import InMemoryConnectorStore, InMemoryRateLimiter, InMemoryTenancyStore

pytestmark = pytest.mark.application

WS = "ws1"


class Clock:
    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def _service(store: InMemoryConnectorStore, clock: Clock, **options: Any) -> ConnectorService:
    return ConnectorService(
        store,
        rate_limiter=options.pop("rate_limiter", InMemoryRateLimiter()),
        now=clock,
        step_seconds=0.001,
        **options,
    )


def _device(store: InMemoryConnectorStore, clock: Clock, device_id: str = "dev_1") -> str:
    credential = c.generate_credential()
    store.devices_by_id[device_id] = c.Device(
        id=device_id,
        workspace_id=WS,
        name="Laptop",
        registered_by="owner",
        status=c.DeviceStatus.ACTIVE,
        credential_hash=hash_token(credential),
        credential_issued_at=clock(),
        created_at=clock(),
        connector_version="1.0.0",
        last_seen_at=clock(),
    )
    return credential


def _waiting(
    store: InMemoryConnectorStore,
    request_id: str,
    created_at: datetime,
    *,
    device_id: str = "dev_1",
    status: c.RequestStatus = c.RequestStatus.WAITING,
    project_id: str | None = None,
    workspace_id: str = WS,
) -> None:
    store.requests[request_id] = c.ChannelRequest(
        id=request_id,
        workspace_id=workspace_id,
        device_id=device_id,
        actor_user_id="owner",
        kind="POST /chat/completions",
        status=status,
        created_at=created_at,
        payload={"method": "POST", "path": "/chat/completions", "body": {"m": "Wren ran."}},
        project_id=project_id,
    )


async def _poll(service: ConnectorService, credential: str, *, wait: float = 0.0, take=True):
    caller = await service.authenticate(credential, version="1.0.0")
    return await service.poll(caller, version="1.0.0", capabilities=None, wait=wait, take=take)


# --- words a crash left behind (L1) --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_nobody_waits_for_any_more_is_never_handed_to_the_computer():
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock)
    credential = _device(store, clock)
    # Left by a server stopped mid-request: older than the pickup window.
    _waiting(store, "creq_old", clock.now - timedelta(seconds=c.PICKUP_SECONDS + 1))
    assert (await _poll(service, credential)).request is None
    _waiting(store, "creq_new", clock.now)
    taken = (await _poll(service, credential)).request
    assert taken is not None and taken.id == "creq_new"


@pytest.mark.asyncio
async def test_the_words_a_crash_left_in_the_queue_are_cleared():
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock)
    credential = _device(store, clock)
    long_ago = clock.now - timedelta(seconds=c.PICKUP_SECONDS + c.ANSWER_SECONDS + 1)
    _waiting(store, "creq_waiting", long_ago)
    _waiting(store, "creq_taken", long_ago, status=c.RequestStatus.TAKEN)
    # Answered, but the waiter died before reading (and clearing) the answer.
    _waiting(store, "creq_unread", clock.now - timedelta(minutes=10), status=c.RequestStatus.DONE)
    store.requests["creq_unread"].result = {"choices": ["The tide turned."]}
    store.requests["creq_unread"].finished_at = clock.now - timedelta(minutes=9)
    # Still in time: left alone.
    _waiting(store, "creq_running", clock.now - timedelta(seconds=30), status=c.RequestStatus.TAKEN)

    await _poll(service, credential)

    for request_id in ("creq_waiting", "creq_taken"):
        left = store.requests[request_id]
        assert left.payload is None and left.status is c.RequestStatus.EXPIRED
        assert left.failure == "abandoned"
    unread = store.requests["creq_unread"]
    assert unread.result is None and unread.payload is None
    assert unread.status is c.RequestStatus.DONE
    assert store.requests["creq_running"].payload is not None


@pytest.mark.asyncio
async def test_a_relayed_request_sweeps_the_queue_and_names_its_story():
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock, pickup_seconds=0.02)
    _device(store, clock)
    _waiting(store, "creq_old", clock.now - timedelta(hours=2))
    from backend.services import connector_service as module

    module._request_project.set("novel")
    with pytest.raises(Exception, match="did not pick up"):
        await service.relay(WS, "dev_1", method="GET", path="/models", body=None)
    assert store.requests["creq_old"].payload is None
    (mine,) = [r for r in store.requests.values() if r.id != "creq_old"]
    assert mine.project_id == "novel"


@pytest.mark.asyncio
async def test_deleting_a_story_removes_the_requests_made_for_it(fake_container):
    store = fake_container.connector_store
    now, local = utcnow(), "local-workspace"
    _waiting(store, "creq_story", now, project_id="novel", workspace_id=local)
    _waiting(store, "creq_other", now, project_id="another", workspace_id=local)
    _waiting(store, "creq_elsewhere", now, project_id="novel")
    counts = await fake_container.tenancy_service._purger.purge_project(local, "novel")
    assert counts["connector_requests"] == 1
    assert set(store.requests) == {"creq_other", "creq_elsewhere"}


# --- a disconnected computer takes nothing (L7) --------------------------------------------


@pytest.mark.asyncio
async def test_a_disconnected_computer_is_never_handed_a_request():
    store, clock = InMemoryConnectorStore(), Clock()
    _device(store, clock)
    _waiting(store, "creq_1", clock.now)
    store.devices_by_id["dev_1"].status = c.DeviceStatus.REVOKED
    found = await store.take_next(
        device_id="dev_1", at=clock.now, since=clock.now - timedelta(minutes=1)
    )
    assert found is None
    assert store.requests["creq_1"].status is c.RequestStatus.WAITING


# --- a copied credential (L3) --------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotating_with_the_replaced_credential_once_the_new_one_is_in_use_disconnects():
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock)
    first = _device(store, clock)
    second = await service.rotate(await service.authenticate(first, version="1.0.0"))
    # The computer uses its new credential ...
    await _poll(service, second)
    # ... and someone holding a copy of the old one tries to rotate inside the grace window.
    copied = await service.authenticate(first, version="1.0.0")
    assert copied.previous
    with pytest.raises(UnauthorizedError):
        await service.rotate(copied)
    device = store.devices_by_id["dev_1"]
    assert device.status is c.DeviceStatus.REVOKED
    assert device.revoked_reason is c.RevokedBecause.REUSED
    for credential in (first, second):
        with pytest.raises(UnauthorizedError):
            await service.authenticate(credential, version="1.0.0")


@pytest.mark.asyncio
async def test_a_lost_rotation_answer_can_be_retried_without_dropping_the_new_credential():
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock)
    first = _device(store, clock)
    # The answer carrying this one never reached the computer.
    lost = await service.rotate(await service.authenticate(first, version="1.0.0"))
    retried = await service.rotate(await service.authenticate(first, version="1.0.0"))
    assert store.devices_by_id["dev_1"].status is c.DeviceStatus.ACTIVE
    assert (await _poll(service, retried)).rotate is False
    # The current one is kept as the previous one, never dropped for the one presented.
    assert (await service.authenticate(lost, version="1.0.0")).previous
    with pytest.raises(UnauthorizedError):
        await service.authenticate(first, version="1.0.0")


@pytest.mark.asyncio
async def test_using_a_credential_records_when_it_was_last_used():
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock)
    credential = _device(store, clock)
    assert store.devices_by_id["dev_1"].credential_last_used_at is None
    await service.authenticate(credential, version="1.0.0")
    assert store.devices_by_id["dev_1"].credential_last_used_at == clock.now
    clock.advance(minutes=2)
    await service.authenticate(credential, version="1.0.0")
    assert store.devices_by_id["dev_1"].credential_last_used_at == clock.now


# --- a member who is no longer an admin (L6) -----------------------------------------------


class Members:
    """Tenancy as the service sees it: the connecting member is an admin, or not."""

    def __init__(self) -> None:
        self.admin = True

    async def require_permission(self, scope, workspace_id, permission):
        if not self.admin:
            raise ForbiddenError("no")

    async def record_workspace_audit(self, *args, **kwargs):
        return None


class HostedSettings:
    is_hosted = True


@pytest.mark.asyncio
async def test_a_computer_stops_when_the_member_who_connected_it_is_no_longer_an_admin():
    store, clock = InMemoryConnectorStore(), Clock()
    members = Members()
    service = _service(store, clock, tenancy=members, settings=HostedSettings())
    credential = _device(store, clock)
    await service.authenticate(credential, version="1.0.0")
    members.admin = False
    with pytest.raises(UnauthorizedError):
        await service.authenticate(credential, version="1.0.0")
    assert store.devices_by_id["dev_1"].revoked_reason is c.RevokedBecause.ACCESS_LOST


@pytest.mark.asyncio
async def test_demoting_an_owner_disconnects_the_computers_they_connected():
    tenancy_store = InMemoryTenancyStore()
    tenancy = TenancyService(tenancy_store)
    owner = AccessScope(actor_user_id="owner", auth_mode=AuthMode.LOCAL)
    workspace = await tenancy.create_workspace(owner, slug="harbor", name="Harbor")
    await tenancy_store.add_membership(workspace_id=workspace.id, user_id="ada", role=Role.OWNER)
    withdrawn: list[tuple[str, str]] = []

    class Devices:
        async def withdraw_for_member(self, scope, workspace_id, user_id):
            withdrawn.append((workspace_id, user_id))
            return []

    tenancy.attach_member_devices(Devices())
    await tenancy.set_role(owner, workspace.id, "ada", Role.OWNER)
    assert withdrawn == []
    await tenancy.set_role(owner, workspace.id, "ada", Role.EDITOR)
    assert withdrawn == [(workspace.id, "ada")]


# --- polls held open (L4) -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_computer_may_hold_only_two_polls_open_at_once():
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock)
    credential = _device(store, clock)
    held = [asyncio.create_task(_poll(service, credential, wait=0.3)) for _ in range(2)]
    await asyncio.sleep(0.05)
    with pytest.raises(RateLimitedError):
        await _poll(service, credential, wait=0.3)
    await asyncio.gather(*held)
    # Once they are answered, polling works again.
    assert (await _poll(service, credential)).request is None


@pytest.mark.asyncio
async def test_a_computer_may_poll_only_so_often_in_a_minute():
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock)
    credential = _device(store, clock)
    for _ in range(c.POLLS_PER_MINUTE):
        await _poll(service, credential, take=False)
    with pytest.raises(RateLimitedError):
        await _poll(service, credential, take=False)


@pytest.mark.asyncio
async def test_an_idle_poll_checks_the_queue_less_and_less_often():
    store, clock = InMemoryConnectorStore(), Clock()
    service = ConnectorService(store, now=clock, step_seconds=0.01, max_step_seconds=0.08)
    credential = _device(store, clock)
    checks = 0
    take_next = store.take_next

    async def counted(**kwargs):
        nonlocal checks
        checks += 1
        return await take_next(**kwargs)

    store.take_next = counted  # type: ignore[method-assign]
    await _poll(service, credential, wait=0.5)
    # Every 10 ms would be about 50 checks; growing to 80 ms apart it is under 15.
    assert checks < 15


# --- answers too large to keep (L5) --------------------------------------------------------


@pytest.mark.asyncio
async def test_an_answer_is_measured_as_received_and_one_too_large_ends_its_request(
    monkeypatch,
):
    store, clock = InMemoryConnectorStore(), Clock()
    service = _service(store, clock)
    credential = _device(store, clock)
    _waiting(store, "creq_1", clock.now)
    taken = (await _poll(service, credential)).request
    assert taken is not None
    monkeypatch.setattr(c, "MAX_ANSWER_BYTES", 1000)
    caller = await service.authenticate(credential, version="1.0.0")
    with pytest.raises(AnswerTooLarge):
        await service.answer(
            caller, "creq_1", status_code=200, body={"text": "é" * 600}, failure=None
        )
    ended = store.requests["creq_1"]
    assert ended.status is c.RequestStatus.FAILED and ended.failure == "too_large"
    assert ended.result is None
