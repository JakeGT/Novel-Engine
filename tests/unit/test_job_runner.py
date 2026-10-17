"""The job runner (ADR-022): claims, re-checks access, works in the right line of the
story, stops when asked, and records every ending in plain words."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.domain.branching import working_branch_id
from backend.domain.errors import (
    CapabilityUnavailableError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.jobs import JobAuthorizationError, JobStatus
from backend.ports import AIInvalidOutputError, AIProviderError
from backend.services.job_runner import JobRunner, JobSubmission
from tests.doubles import InMemoryJobStore

pytestmark = pytest.mark.unit

WS, PR = "ws_jobs", "pr_jobs"
SCOPE = AccessScope(
    actor_user_id="usr_author", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
)


class _Journal:
    def __init__(self) -> None:
        self.branches = {
            "branch_main": SimpleNamespace(id="branch_main", is_root=True),
            "branch_whatif": SimpleNamespace(id="branch_whatif", is_root=False),
        }

    async def get_branch(self, *, workspace_id, project_id, branch_id):
        return self.branches.get(branch_id)


class _Tenancy:
    """Hosted access: the author may write until ``allowed`` is turned off."""

    def __init__(self) -> None:
        self.allowed = True
        self.checks: list[str] = []

    async def require_project(self, scope, workspace_id, project_id, permission):
        self.checks.append(permission.value)
        if not self.allowed:
            raise DomainError("forbidden")

    async def require_membership(self, scope, workspace_id):
        return SimpleNamespace(role=SimpleNamespace(value="editor"))


def _runner(store=None, **kwargs) -> tuple[JobRunner, InMemoryJobStore]:
    store = store or InMemoryJobStore()
    runner = JobRunner(store=store, journal=_Journal(), local_mode=True, autostart=False, **kwargs)
    return runner, store


def _submit(runner, kind="work", payload=None, key="", scope=SCOPE):
    return asyncio.run(
        runner.submit(
            scope, WS, PR, JobSubmission(kind=kind, payload=payload or {}, idempotency_key=key)
        )
    )


def test_a_job_runs_reports_progress_and_usage_and_succeeds() -> None:
    runner, store = _runner()
    seen: list[str] = []

    async def work(ctx):
        seen.append(ctx.scope.actor_user_id)
        await ctx.step("Halfway", 50)
        assert store.jobs[(WS, PR, ctx.job.id)].stage == "Halfway"
        await ctx.used(120, 30)
        await ctx.used(10, 5)
        return {"made": 1}

    runner.register("work", work)
    job = _submit(runner, payload={"plan_id": "plan_1"})
    assert job.status is JobStatus.QUEUED and job.policy_version and job.idempotency_key == ""
    done = asyncio.run(runner.run_once())
    assert done.id == job.id and done.status is JobStatus.SUCCEEDED
    assert done.result == {"made": 1} and done.progress == 100 and done.finished_at
    assert (done.prompt_tokens, done.completion_tokens) == (130, 35)
    assert done.attempts == 1 and seen == ["usr_author"]
    # Nothing is left to run.
    assert asyncio.run(runner.run_once()) is None


def test_run_pending_runs_waiting_jobs_oldest_first() -> None:
    runner, _ = _runner()
    order: list[str] = []

    async def work(ctx):
        order.append(ctx.job.payload["n"])
        return {}

    runner.register("work", work)
    for n in ("a", "b", "c"):
        _submit(runner, payload={"n": n})
    done = asyncio.run(runner.run_pending())
    assert order == ["a", "b", "c"] and [j.status for j in done] == [JobStatus.SUCCEEDED] * 3
    assert asyncio.run(runner.run_pending()) == []


def test_the_same_key_gives_back_the_job_already_asked_for() -> None:
    runner, store = _runner()
    runner.register("work", lambda ctx: asyncio.sleep(0, {}))
    first = _submit(runner, key="k1")
    again = _submit(runner, key="k1")
    other = _submit(runner, key="k2")
    someone_else = _submit(runner, key="k1", scope=replace(SCOPE, actor_user_id="usr_other"))
    assert first.id == again.id
    assert len({first.id, other.id, someone_else.id}) == 3 and len(store.jobs) == 3


def test_a_kind_nobody_runs_is_refused_and_a_stored_one_fails_plainly() -> None:
    runner, store = _runner()
    with pytest.raises(ValidationError):
        _submit(runner, kind="mystery")
    assert store.jobs == {}

    runner.register("work", lambda ctx: asyncio.sleep(0, {}))
    job = _submit(runner)
    # A job left by an older version of the app whose kind is no longer known.
    store.jobs[(WS, PR, job.id)] = replace(job, kind="retired_kind")
    done = asyncio.run(runner.run_once())
    assert done.status is JobStatus.FAILED and done.error_code == "refused"
    assert done.error_message == "That kind of work cannot be done here."


def test_a_waiting_job_cancelled_never_runs() -> None:
    runner, store = _runner()
    ran: list[str] = []

    async def work(ctx):
        ran.append(ctx.job.id)
        return {}

    runner.register("work", work)
    job = _submit(runner)
    cancelled = asyncio.run(store.request_cancel(workspace_id=WS, project_id=PR, job_id=job.id))
    assert cancelled.status is JobStatus.CANCELLED and cancelled.finished_at is not None
    assert asyncio.run(runner.run_once()) is None and ran == []


def test_a_running_job_stops_at_its_next_step_when_asked() -> None:
    runner, store = _runner()
    steps: list[str] = []

    async def work(ctx):
        await ctx.step("one", 10)
        steps.append("one")
        await store.request_cancel(workspace_id=WS, project_id=PR, job_id=ctx.job.id)
        await ctx.step("two", 50)
        steps.append("two")
        return {"finished": True}

    runner.register("work", work)
    job = _submit(runner)
    done = asyncio.run(runner.run_once())
    assert done.status is JobStatus.CANCELLED and done.result == {}
    assert steps == ["one"] and done.error_code is None
    # Asking again once it has ended changes nothing.
    after = asyncio.run(store.request_cancel(workspace_id=WS, project_id=PR, job_id=job.id))
    assert after.status is JobStatus.CANCELLED


def test_a_worker_that_lost_its_job_stops_and_does_not_overwrite_its_ending() -> None:
    runner, store = _runner()

    async def work(ctx):
        # Another worker took the job over and finished it meanwhile.
        key = (WS, PR, ctx.job.id)
        store.owners[key] = "worker_other"
        await store.finish(
            store.jobs[key],
            owner="worker_other",
            status=JobStatus.SUCCEEDED,
            result={"by": "other"},
        )
        await ctx.step("next", 60)
        return {"by": "me"}

    runner.register("work", work)
    _submit(runner)
    done = asyncio.run(runner.run_once())
    assert done.status is JobStatus.SUCCEEDED and done.result == {"by": "other"}


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (CapabilityUnavailableError("no key: sk-live-123"), "no_provider", "No AI key"),
        (AIProviderError("HTTP 529 from upstream sk-live-123"), "provider", "did not answer"),
        (AIInvalidOutputError("bad JSON sk-live-123"), "provider_output", "could not be used"),
        (JobAuthorizationError("stale sk-live-123"), "not_allowed", "no longer have permission"),
        (NotFoundError("missing sk-live-123"), "not_found", "no longer there"),
        (ConflictError("This scene changed."), "refused", "This scene changed."),
        (RuntimeError("Traceback sk-live-123"), "internal", "Something went wrong"),
        (KeyError("plan_id"), "internal", "Something went wrong"),
    ],
)
def test_failures_are_recorded_in_plain_words(error, code, message, caplog) -> None:
    runner, _ = _runner()

    async def work(ctx):
        await ctx.step("Working", 20)
        raise error

    runner.register("work", work)
    _submit(runner, payload={"direction": "The tide turns at midnight, secretly."})
    with caplog.at_level(logging.DEBUG):
        done = asyncio.run(runner.run_once())
    assert done.status is JobStatus.FAILED and done.error_code == code
    assert message in done.error_message
    if code != "refused":
        assert "sk-live-123" not in done.error_message
    assert done.progress == 20 and done.finished_at is not None
    # Neither the author's words nor a provider's message reach the logs.
    assert "The tide turns" not in caplog.text and "sk-live-123" not in caplog.text


def test_a_stop_from_outside_is_not_swallowed_and_the_job_goes_back_in_the_queue() -> None:
    runner, store = _runner()
    runs: list[int] = []

    async def work(ctx):
        runs.append(ctx.job.attempts)
        if len(runs) == 1:
            raise asyncio.CancelledError
        return {"ok": True}

    runner.register("work", work)
    job = _submit(runner)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.run_once())
    # Not left held until its lease runs out, and the interrupted run is not counted.
    waiting = store.jobs[(WS, PR, job.id)]
    assert waiting.status is JobStatus.QUEUED and waiting.attempts == 0
    assert store.owners[(WS, PR, job.id)] is None
    done = asyncio.run(runner.run_once())
    assert done.status is JobStatus.SUCCEEDED and runs == [1, 1]


def test_stopping_the_runner_mid_job_puts_the_job_back() -> None:
    store = InMemoryJobStore()
    runner = JobRunner(store=store, journal=_Journal(), local_mode=True)
    started = asyncio.Event()

    async def work(ctx):
        started.set()
        await asyncio.sleep(60)
        return {}

    runner.register("work", work)

    async def scenario():
        job = await runner.submit(SCOPE, WS, PR, JobSubmission(kind="work", payload={}))
        runner.start()
        await asyncio.wait_for(started.wait(), timeout=5)
        await runner.stop()
        return store.jobs[(WS, PR, job.id)]

    stopped = asyncio.run(scenario())
    assert stopped.status is JobStatus.QUEUED and stopped.attempts == 0


def test_a_worker_that_lost_its_lease_stops_quietly_and_never_cancels(caplog) -> None:
    runner, store = _runner()
    after: list[str] = []

    async def work(ctx):
        key = (WS, PR, ctx.job.id)
        # Another worker took the job over while this one was busy.
        store.owners[key] = "worker_other"
        await ctx.used(500, 50)
        try:
            await ctx.step("next", 60)
        finally:
            after.append("stopped")
        after.append("went on")
        return {"by": "me"}

    runner.register("work", work)
    job = _submit(runner)
    with caplog.at_level(logging.INFO):
        done = asyncio.run(runner.run_once())
    # Still running under the other worker: neither cancelled nor ended by this one.
    assert done.status is JobStatus.RUNNING and done.result == {}
    assert after == ["stopped"] and "job.lease_lost" in caplog.text
    assert (done.prompt_tokens, done.completion_tokens) == (0, 0)
    # The worker that holds it now ends it as usual.
    ended = asyncio.run(
        store.finish(store.jobs[(WS, PR, job.id)], owner="worker_other", status=JobStatus.SUCCEEDED)
    )
    assert ended.status is JobStatus.SUCCEEDED


def test_a_worker_that_lost_its_lease_cannot_end_the_job() -> None:
    runner, store = _runner()
    runner.register("work", lambda ctx: asyncio.sleep(0, {}))
    job = _submit(runner)
    claimed = asyncio.run(store.claim(owner="worker_a"))
    still = asyncio.run(store.finish(claimed, owner="worker_b", status=JobStatus.CANCELLED))
    assert still.status is JobStatus.RUNNING
    assert asyncio.run(
        store.finish(claimed, owner="worker_a", status=JobStatus.CANCELLED)
    ).status is (JobStatus.CANCELLED)
    assert job.id == claimed.id


def test_the_lease_is_kept_while_a_slow_call_runs(monkeypatch) -> None:
    runner, store = _runner()
    beats: list[tuple[str, int]] = []
    heartbeat = store.heartbeat

    async def counting(job, *, owner, stage, progress):
        beats.append((stage, progress))
        return await heartbeat(job, owner=owner, stage=stage, progress=progress)

    monkeypatch.setattr(store, "heartbeat", counting)

    async def work(ctx):
        ctx._renew_seconds = 0.01
        await ctx.step("Writing", 30)
        async with ctx.holding():
            await asyncio.sleep(0.08)
        return {}

    runner.register("work", work)
    _submit(runner)
    done = asyncio.run(runner.run_once())
    assert done.status is JobStatus.SUCCEEDED
    # Renewed in the background, keeping the last stage the author was shown.
    assert len(beats) >= 3 and set(beats) == {("Writing", 30)}


def test_a_lease_lost_during_a_slow_call_stops_the_job_after_it() -> None:
    runner, store = _runner()
    after: list[str] = []

    async def work(ctx):
        ctx._renew_seconds = 0.01
        await ctx.step("Writing", 30)
        async with ctx.holding():
            store.owners[(WS, PR, ctx.job.id)] = "worker_other"
            await asyncio.sleep(0.05)
        after.append("went on")
        return {}

    runner.register("work", work)
    _submit(runner)
    done = asyncio.run(runner.run_once())
    assert done.status is JobStatus.RUNNING and after == []


def test_internal_failures_are_logged_by_kind_only(caplog) -> None:
    runner, _ = _runner()

    async def work(ctx):
        raise RuntimeError("She whispered the secret name: Aldermere.")

    runner.register("work", work)
    _submit(runner)
    with caplog.at_level(logging.DEBUG):
        done = asyncio.run(runner.run_once())
    assert done.error_code == "internal"
    record = next(r for r in caplog.records if r.getMessage() == "job.failed.internal")
    assert record.levelno == logging.WARNING
    assert record.fields == {"job_kind": "work", "error_type": "RuntimeError"}
    assert "Aldermere" not in caplog.text
    assert all("Aldermere" not in str(getattr(r, "fields", "")) for r in caplog.records)


def test_one_person_may_have_only_a_few_jobs_waiting_in_a_project() -> None:
    from backend.domain.errors import RateLimitedError
    from backend.domain.jobs import MAX_ACTIVE_JOBS

    runner, store = _runner()
    runner.register("work", lambda ctx: asyncio.sleep(0, {}))
    first = _submit(runner, key="k0")
    for n in range(1, MAX_ACTIVE_JOBS):
        _submit(runner, key=f"k{n}")
    with pytest.raises(RateLimitedError) as refused:
        _submit(runner, key="one-more")
    assert "Wait for one to finish" in refused.value.detail
    assert len(store.jobs) == MAX_ACTIVE_JOBS
    # Asking again with a key already used still gives back that job.
    assert _submit(runner, key="k0").id == first.id
    # Someone else, or another project, is not held back.
    _submit(runner, scope=replace(SCOPE, actor_user_id="usr_other"))
    asyncio.run(
        runner.submit(
            replace(SCOPE, project_id="pr_other"), WS, "pr_other", JobSubmission("work", {})
        )
    )
    # Once one ends there is room again.
    asyncio.run(runner.run_once())
    _submit(runner, key="one-more")


def test_a_job_works_in_the_line_of_the_story_it_was_asked_in() -> None:
    runner, _ = _runner()
    seen: list[str | None] = []

    async def work(ctx):
        seen.append(working_branch_id())
        return {}

    runner.register("work", work)
    _submit(runner, payload={"branch_id": "branch_whatif"})
    _submit(runner, payload={"branch_id": "branch_main"})
    _submit(runner, payload={})
    asyncio.run(runner.run_pending())
    assert seen == ["branch_whatif", None, None]
    assert working_branch_id() is None


def test_a_what_if_that_is_gone_fails_the_job_as_not_found() -> None:
    runner, _ = _runner()
    ran: list[str] = []

    async def work(ctx):
        ran.append(ctx.job.id)
        return {}

    runner.register("work", work)
    _submit(runner, payload={"branch_id": "branch_deleted"})
    done = asyncio.run(runner.run_once())
    assert done.status is JobStatus.FAILED and done.error_code == "not_found"
    assert ran == []


def test_a_job_from_an_older_access_policy_is_not_run() -> None:
    runner, store = _runner()
    ran: list[str] = []

    async def work(ctx):
        ran.append(ctx.job.id)
        return {}

    runner.register("work", work)
    job = _submit(runner)
    store.jobs[(WS, PR, job.id)] = replace(job, policy_version="0")
    done = asyncio.run(runner.run_once())
    assert done.error_code == "not_allowed" and ran == []


def test_an_old_local_job_is_checked_like_any_other_on_a_hosted_install() -> None:
    tenancy = _Tenancy()
    store = InMemoryJobStore()
    runner = JobRunner(store=store, journal=_Journal(), tenancy=tenancy, autostart=False)
    ran: list[str] = []

    async def work(ctx):
        ran.append(ctx.job.id)
        return {}

    runner.register("work", work)
    job = _submit(runner, scope=replace(SCOPE, auth_mode=AuthMode.HOSTED))
    # Left from when the install ran locally.
    store.jobs[(WS, PR, job.id)] = replace(job, auth_mode=AuthMode.LOCAL)
    tenancy.allowed = False
    done = asyncio.run(runner.run_once())
    assert done.error_code == "not_allowed" and ran == []


def test_hosted_access_is_checked_when_asked_and_again_when_run() -> None:
    tenancy = _Tenancy()
    store = InMemoryJobStore()
    runner = JobRunner(store=store, journal=_Journal(), tenancy=tenancy, autostart=False)
    ran: list[str] = []

    async def work(ctx):
        ran.append(ctx.job.id)
        return {}

    runner.register("work", work)
    hosted = replace(SCOPE, auth_mode=AuthMode.HOSTED)
    first = _submit(runner, scope=hosted)
    assert first.role == "editor" and tenancy.checks == ["project.write"]
    assert asyncio.run(runner.run_once()).status is JobStatus.SUCCEEDED
    assert tenancy.checks == ["project.write", "project.write"]

    second = _submit(runner, scope=hosted)
    tenancy.allowed = False
    done = asyncio.run(runner.run_once())
    assert done.id == second.id and done.error_code == "not_allowed"
    assert ran == [first.id]
    # Nobody without access can ask in the first place.
    with pytest.raises(DomainError):
        _submit(runner, scope=hosted)
