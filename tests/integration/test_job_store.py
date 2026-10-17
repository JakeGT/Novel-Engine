"""Stored jobs in PostgreSQL (ADR-022): one worker per job, leases that run out, and
endings that stay as they were recorded."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.job_store import PostgresJobStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.identity import AuthMode
from backend.domain.jobs import (
    AUTHORIZATION_POLICY_VERSION,
    MAX_ATTEMPTS,
    Job,
    JobStatus,
    LeaseLost,
)

pytestmark = pytest.mark.integration

WS, PR = "ws_jobs_it", "pr_jobs_it"


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield PostgresJobStore(factory), factory
    finally:
        await engine.dispose()


def _job(job_id: str, *, key: str = "", actor: str = "usr_a", payload: dict | None = None) -> Job:
    return Job(
        workspace_id=WS,
        project_id=PR,
        id=job_id,
        kind="scene_draft",
        status=JobStatus.QUEUED,
        actor_user_id=actor,
        auth_mode=AuthMode.LOCAL,
        policy_version=AUTHORIZATION_POLICY_VERSION,
        role="owner",
        payload=payload or {"plan_id": "plan_1", "variants": 2},
        correlation_id="corr_1",
        idempotency_key=key,
    )


async def _lease(factory, job_id: str) -> tuple[str | None, bool]:
    """(owner, whether the lease is still running)."""
    async with session_scope(factory) as db:
        row = (
            await db.execute(
                text(
                    "SELECT lease_owner, lease_expires_at > now() AS live FROM jobs "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                ),
                {"w": WS, "p": PR, "id": job_id},
            )
        ).one()
    return row.lease_owner, bool(row.live)


async def _expire(factory, job_id: str) -> None:
    """The worker holding the job stopped renewing its lease (it died)."""
    async with session_scope(factory) as db:
        await db.execute(
            text(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE workspace_id = :w AND project_id = :p AND id = :id"
            ),
            {"w": WS, "p": PR, "id": job_id},
        )


@pytest.mark.asyncio
async def test_a_job_is_stored_as_given_and_the_same_key_returns_it(stack) -> None:
    store, _ = stack
    made = await store.create(_job("job_1", key="click-1"))
    assert made.status is JobStatus.QUEUED and made.payload == {"plan_id": "plan_1", "variants": 2}
    assert (made.attempts, made.progress, made.cancel_requested) == (0, 0, False)
    assert made.role == "owner" and made.correlation_id == "corr_1" and made.finished_at is None

    again = await store.create(_job("job_2", key="click-1"))
    assert again.id == "job_1"
    # Another person's key, and no key at all, are never matched.
    assert (await store.create(_job("job_3", key="click-1", actor="usr_b"))).id == "job_3"
    assert (await store.create(_job("job_4"))).id == "job_4"
    assert (await store.create(_job("job_5"))).id == "job_5"
    assert await store.get(workspace_id=WS, project_id=PR, job_id="job_2") is None
    assert (await store.get(workspace_id=WS, project_id=PR, job_id="job_4")).kind == "scene_draft"
    assert await store.get(workspace_id="ws_other", project_id=PR, job_id="job_1") is None


@pytest.mark.asyncio
async def test_two_requests_with_the_same_key_at_once_get_one_job(stack) -> None:
    store, factory = stack
    made = await asyncio.gather(*(store.create(_job(f"job_{n}", key="double")) for n in range(6)))
    assert len({job.id for job in made}) == 1
    async with session_scope(factory) as db:
        count = (await db.execute(text("SELECT count(*) FROM jobs"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_recent_lists_one_scenes_jobs_newest_first(stack) -> None:
    store, _ = stack
    for n in range(3):
        await store.create(_job(f"job_{n}"))
    await store.create(_job("job_other_scene", payload={"plan_id": "plan_2"}))
    recent = await store.recent(
        workspace_id=WS, project_id=PR, kind="scene_draft", plan_id="plan_1", limit=2
    )
    assert [job.id for job in recent] == ["job_2", "job_1"]
    assert (
        await store.recent(workspace_id=WS, project_id=PR, kind="scene_review", plan_id="plan_1")
        == []
    )


@pytest.mark.asyncio
async def test_claims_take_the_oldest_waiting_job_and_hold_it(stack) -> None:
    store, factory = stack
    for n in range(3):
        await store.create(_job(f"job_{n}"))
    first = await store.claim(owner="worker_a")
    assert first.id == "job_0" and first.status is JobStatus.RUNNING and first.attempts == 1
    assert await _lease(factory, "job_0") == ("worker_a", True)
    second = await store.claim(owner="worker_b")
    assert second.id == "job_1"
    assert (await store.claim(owner="worker_a")).id == "job_2"
    assert await store.claim(owner="worker_a") is None


@pytest.mark.asyncio
async def test_workers_claiming_at_once_never_take_the_same_job(stack) -> None:
    store, _ = stack
    for n in range(8):
        await store.create(_job(f"job_{n}"))
    claimed = await asyncio.gather(*(store.claim(owner=f"worker_{n}") for n in range(12)))
    taken = [job.id for job in claimed if job is not None]
    assert len(taken) == len(set(taken)) == 8
    assert claimed.count(None) == 4


@pytest.mark.asyncio
async def test_a_job_whose_worker_died_is_taken_over_then_given_up(stack) -> None:
    store, factory = stack
    await store.create(_job("job_1"))
    await store.claim(owner="worker_a")
    # Still held: nobody else may take it.
    assert await store.claim(owner="worker_b") is None

    await _expire(factory, "job_1")
    taken = await store.claim(owner="worker_b")
    assert taken.id == "job_1" and taken.attempts == 2 == MAX_ATTEMPTS
    assert await _lease(factory, "job_1") == ("worker_b", True)
    # The first worker, back from the dead, finds it has lost the job, and can neither
    # end it nor add to its usage.
    with pytest.raises(LeaseLost):
        await store.heartbeat(taken, owner="worker_a", stage="Writing", progress=40)
    late = await store.finish(taken, owner="worker_a", status=JobStatus.CANCELLED)
    assert late.status is JobStatus.RUNNING and late.finished_at is None
    await store.add_usage(taken, owner="worker_a", prompt_tokens=900, completion_tokens=90)
    assert (await store.get(workspace_id=WS, project_id=PR, job_id="job_1")).prompt_tokens == 0
    assert await _lease(factory, "job_1") == ("worker_b", True)

    await _expire(factory, "job_1")
    # Interrupted too often: given up rather than tried for ever.
    assert await store.claim(owner="worker_c") is None
    gone = await store.get(workspace_id=WS, project_id=PR, job_id="job_1")
    assert gone.status is JobStatus.FAILED and gone.error_code == "interrupted"
    assert gone.error_message and gone.finished_at is not None
    assert await _lease(factory, "job_1") == (None, False)


@pytest.mark.asyncio
async def test_a_heartbeat_renews_the_lease_records_progress_and_says_when_to_stop(stack) -> None:
    store, factory = stack
    await store.create(_job("job_1"))
    job = await store.claim(owner="worker_a")
    await _expire(factory, "job_1")
    assert await store.heartbeat(job, owner="worker_a", stage="x" * 200, progress=140) is False
    assert await _lease(factory, "job_1") == ("worker_a", True)
    now = await store.get(workspace_id=WS, project_id=PR, job_id="job_1")
    assert len(now.stage) == 120 and now.progress == 100

    await store.add_usage(job, owner="worker_a", prompt_tokens=100, completion_tokens=20)
    await store.add_usage(job, owner="worker_a", prompt_tokens=5, completion_tokens=1)
    cancelled = await store.request_cancel(workspace_id=WS, project_id=PR, job_id="job_1")
    # A running job is not ended by the request; it stops at its next step.
    assert cancelled.status is JobStatus.RUNNING and cancelled.cancel_requested is True
    assert cancelled.finished_at is None
    assert await store.heartbeat(job, owner="worker_a", stage="Writing", progress=50) is True
    ended = await store.finish(job, owner="worker_a", status=JobStatus.CANCELLED)
    assert ended.status is JobStatus.CANCELLED and ended.progress == 50
    assert (ended.prompt_tokens, ended.completion_tokens) == (105, 21)
    assert await _lease(factory, "job_1") == (None, False)


@pytest.mark.asyncio
async def test_cancelling_a_waiting_job_ends_it_and_it_is_never_claimed(stack) -> None:
    store, _ = stack
    await store.create(_job("job_1"))
    cancelled = await store.request_cancel(workspace_id=WS, project_id=PR, job_id="job_1")
    assert cancelled.status is JobStatus.CANCELLED and cancelled.cancel_requested is True
    assert cancelled.finished_at is not None
    assert await store.claim(owner="worker_a") is None
    assert await store.request_cancel(workspace_id=WS, project_id=PR, job_id="job_none") is None


@pytest.mark.asyncio
async def test_an_ending_is_recorded_once_and_kept(stack) -> None:
    store, factory = stack
    await store.create(_job("job_1"))
    job = await store.claim(owner="worker_a")
    done = await store.finish(
        job, owner="worker_a", status=JobStatus.SUCCEEDED, result={"drafts": 2}
    )
    assert done.status is JobStatus.SUCCEEDED and done.result == {"drafts": 2}
    assert done.progress == 100 and done.finished_at is not None
    assert done.error_code is None and await _lease(factory, "job_1") == (None, False)

    # A late cancel, or a late ending from a worker that lost the job, changes nothing.
    late = await store.request_cancel(workspace_id=WS, project_id=PR, job_id="job_1")
    assert late.status is JobStatus.SUCCEEDED and late.cancel_requested is False
    overwritten = await store.finish(
        job, owner="worker_a", status=JobStatus.FAILED, error_code="internal", error_message="late"
    )
    assert overwritten.status is JobStatus.SUCCEEDED and overwritten.result == {"drafts": 2}
    assert overwritten.error_code is None

    await store.create(_job("job_2"))
    failing = await store.claim(owner="worker_a")
    await store.heartbeat(failing, owner="worker_a", stage="Writing", progress=30)
    failed = await store.finish(
        failing,
        owner="worker_a",
        status=JobStatus.FAILED,
        error_code="provider",
        error_message="Plain words.",
    )
    assert (failed.status, failed.error_code, failed.error_message, failed.progress) == (
        JobStatus.FAILED,
        "provider",
        "Plain words.",
        30,
    )


@pytest.mark.asyncio
async def test_a_job_given_up_cannot_be_renewed_or_ended_by_its_old_worker(stack) -> None:
    store, factory = stack
    await store.create(_job("job_1"))
    job = await store.claim(owner="worker_a")
    await _expire(factory, "job_1")
    await store.claim(owner="worker_b")
    await _expire(factory, "job_1")
    assert await store.claim(owner="worker_c") is None  # given up
    with pytest.raises(LeaseLost):
        await store.heartbeat(job, owner="worker_b", stage="Writing", progress=50)
    ended = await store.finish(job, owner="worker_b", status=JobStatus.SUCCEEDED)
    assert ended.status is JobStatus.FAILED and ended.error_code == "interrupted"


@pytest.mark.asyncio
async def test_a_job_put_back_is_waiting_again_without_counting_the_run(stack) -> None:
    store, factory = stack
    await store.create(_job("job_1"))
    job = await store.claim(owner="worker_a")
    # Only the worker holding it may put it back.
    await store.requeue(job, owner="worker_b")
    assert (await store.get(workspace_id=WS, project_id=PR, job_id="job_1")).status is (
        JobStatus.RUNNING
    )
    await store.requeue(job, owner="worker_a")
    back = await store.get(workspace_id=WS, project_id=PR, job_id="job_1")
    assert back.status is JobStatus.QUEUED and back.attempts == 0
    assert await _lease(factory, "job_1") == (None, False)
    again = await store.claim(owner="worker_b")
    assert again.id == "job_1" and again.attempts == 1


@pytest.mark.asyncio
async def test_active_jobs_are_counted_per_person_and_found_by_key(stack) -> None:
    store, _ = stack
    for n in range(3):
        await store.create(_job(f"job_{n}", key=f"k{n}"))
    await store.create(_job("job_b", actor="usr_b"))
    assert await store.count_active(workspace_id=WS, project_id=PR, actor_user_id="usr_a") == 3
    claimed = await store.claim(owner="worker_a")
    assert await store.count_active(workspace_id=WS, project_id=PR, actor_user_id="usr_a") == 3
    await store.finish(claimed, owner="worker_a", status=JobStatus.SUCCEEDED)
    await store.request_cancel(workspace_id=WS, project_id=PR, job_id="job_1")
    assert await store.count_active(workspace_id=WS, project_id=PR, actor_user_id="usr_a") == 1
    assert await store.count_active(workspace_id=WS, project_id="pr_x", actor_user_id="usr_a") == 0

    found = await store.find_by_key(
        workspace_id=WS, project_id=PR, actor_user_id="usr_a", idempotency_key="k2"
    )
    assert found is not None and found.id == "job_2"
    for actor, key in (("usr_b", "k2"), ("usr_a", "missing"), ("usr_a", "")):
        assert (
            await store.find_by_key(
                workspace_id=WS, project_id=PR, actor_user_id=actor, idempotency_key=key
            )
            is None
        )


@pytest.mark.asyncio
async def test_recent_can_keep_to_one_line_of_the_story(stack) -> None:
    store, _ = stack
    await store.create(_job("job_main", payload={"plan_id": "plan_1", "branch_id": None}))
    await store.create(_job("job_old", payload={"plan_id": "plan_1"}))
    await store.create(_job("job_whatif", payload={"plan_id": "plan_1", "branch_id": "br_1"}))

    async def ids(**kwargs) -> list[str]:
        found = await store.recent(
            workspace_id=WS, project_id=PR, kind="scene_draft", plan_id="plan_1", **kwargs
        )
        return [job.id for job in found]

    assert await ids() == ["job_whatif", "job_old", "job_main"]
    assert await ids(branch_id=None) == ["job_old", "job_main"]
    assert await ids(branch_id="br_1") == ["job_whatif"]
    assert await ids(branch_id="br_none") == []


@pytest.mark.asyncio
async def test_the_drafts_one_job_made_are_found_by_job(stack) -> None:
    from backend.adapters.studio_store import PostgresStudioStore
    from backend.domain.identity import utcnow
    from backend.domain.studio import SceneDraft

    _, factory = stack
    studio = PostgresStudioStore(factory)
    for draft_id, job_id, number in (("d2", "job_1", 2), ("d1", "job_1", 1), ("d3", "job_2", 1)):
        await studio.add_draft(
            SceneDraft(
                workspace_id=WS,
                project_id=PR,
                id=draft_id,
                branch_id=None,
                plan_id="plan_1",
                job_id=job_id,
                manifest_id=None,
                number=number,
                content="Words.",
                model="fake",
                status="proposed",
                kept_version_id=None,
                created_by="usr_a",
                created_at=utcnow(),
            )
        )
    found = await studio.drafts_for_job(workspace_id=WS, project_id=PR, job_id="job_1")
    assert [(d.id, d.number) for d in found] == [("d1", 1), ("d2", 2)]
    assert await studio.drafts_for_job(workspace_id="ws_x", project_id=PR, job_id="job_1") == []
