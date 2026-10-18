"""Running stored jobs (ADR-022).

A job is claimed under a lease, its scope and the author's access are checked again
before anything runs (the author may have lost access since asking), and it works in
the line of the story it was asked in. Each step renews the lease and is where a
cancellation takes effect. Failures are recorded in plain words; a provider's own error
text never is.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.branching import working_in
from backend.domain.errors import (
    CapabilityUnavailableError,
    DomainError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from backend.domain.identity import AccessScope
from backend.domain.jobs import (
    AUTHORIZATION_POLICY_VERSION,
    MAX_ACTIVE_JOBS,
    RENEW_SECONDS,
    Job,
    JobAuthorizationError,
    JobCancelled,
    JobStatus,
    LeaseLost,
)
from backend.domain.tenancy import Permission
from backend.ports import AIInvalidOutputError, AIProviderError

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0


class JobContext:
    """What a running job may do: report progress, notice cancellation, record usage."""

    def __init__(
        self, store: Any, job: Job, owner: str, *, renew_seconds: float = RENEW_SECONDS
    ) -> None:
        self._store = store
        self.job = job
        self._owner = owner
        self._renew_seconds = renew_seconds
        self._stage = ""
        self._progress = 0
        self._lost = False

    @property
    def scope(self) -> AccessScope:
        return self.job.envelope().scope()

    async def step(self, stage: str, progress: int) -> None:
        """Record progress. Raises ``JobCancelled`` when the author asked the job to stop,
        and ``LeaseLost`` when another worker holds it now."""
        if self._lost:
            raise LeaseLost(self.job.id)
        self._stage, self._progress = stage, progress
        if await self._store.heartbeat(self.job, owner=self._owner, stage=stage, progress=progress):
            raise JobCancelled(stage)

    async def used(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int = 0,
        cost_usd: float | None = None,
    ) -> None:
        extra = {"cost_usd": cost_usd} if cost_usd is not None else {}
        await self._store.add_usage(
            self.job,
            owner=self._owner,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            **extra,
        )

    @asynccontextmanager
    async def holding(self) -> AsyncIterator[None]:
        """Keep the job's lease while something slow (a model call) runs.

        A call can outlast the lease; renewing it in the background stops another worker
        taking the job over from a worker that is still working. A stop asked for
        meanwhile is noticed at the next step, as usual."""
        renewer = asyncio.create_task(self._renew(), name=f"lease-{self.job.id}")
        try:
            yield
        finally:
            renewer.cancel()
            # Waits for the renewer to end; a stop of this task itself still propagates.
            await asyncio.gather(renewer, return_exceptions=True)
        if self._lost:
            raise LeaseLost(self.job.id)

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(self._renew_seconds)
            try:
                await self._store.heartbeat(
                    self.job, owner=self._owner, stage=self._stage, progress=self._progress
                )
            except LeaseLost:
                self._lost = True
                return
            except Exception as error:  # noqa: BLE001 - a missed renewal is tried again
                log_event(
                    logger,
                    "job.lease.renew_failed",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )


Handler = Callable[[JobContext], Awaitable[dict]]

# Plain words for why a job stopped; never a provider's own message.
_FAILURES: list[tuple[type[BaseException], str, str]] = [
    (
        CapabilityUnavailableError,
        "no_provider",
        "No AI key is set up for this workspace, so nothing could be drafted.",
    ),
    (
        AIProviderError,
        "provider",
        "The AI provider did not answer. Nothing was lost; try again later.",
    ),
    (
        AIInvalidOutputError,
        "provider_output",
        "The AI provider's answer could not be used. Nothing was saved from it.",
    ),
    (JobAuthorizationError, "not_allowed", "You no longer have permission to do this."),
    (NotFoundError, "not_found", "Something this needed is no longer there."),
]


@dataclass
class JobSubmission:
    kind: str
    payload: dict
    idempotency_key: str = ""


class JobRunner:
    def __init__(
        self,
        *,
        store: Any,
        journal: Any,
        tenancy: Any = None,
        local_mode: bool = False,
        autostart: bool = True,
    ) -> None:
        self._store = store
        self._journal = journal
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._handlers: dict[str, Handler] = {}
        self._owner = f"worker_{uuid7()}"
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.autostart = autostart

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    async def submit(
        self, scope: AccessScope, workspace_id: str, project_id: str, submission: JobSubmission
    ) -> Job:
        if submission.kind not in self._handlers:
            raise ValidationError("That kind of work cannot be done here.")
        role = ""
        if not self._local_mode and self._tenancy is not None:
            await self._tenancy.require_project(
                scope, workspace_id, project_id, Permission.PROJECT_WRITE
            )
            membership = await self._tenancy.require_membership(scope, workspace_id)
            role = str(getattr(membership.role, "value", membership.role))
        # Asking again with the same key gives back the job already asked for, even when
        # the person is at the limit below.
        existing = await self._store.find_by_key(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=scope.actor_user_id,
            idempotency_key=submission.idempotency_key,
        )
        if existing is not None:
            return existing  # type: ignore[no-any-return]
        active = await self._store.count_active(
            workspace_id=workspace_id, project_id=project_id, actor_user_id=scope.actor_user_id
        )
        if active >= MAX_ACTIVE_JOBS:
            raise RateLimitedError(
                f"You already have {active} pieces of work waiting or under way in this story. "
                "Wait for one to finish, or stop one, then try again."
            )
        job = await self._store.create(
            Job(
                workspace_id=workspace_id,
                project_id=project_id,
                id=f"job_{uuid7()}",
                kind=submission.kind,
                status=JobStatus.QUEUED,
                actor_user_id=scope.actor_user_id,
                auth_mode=scope.auth_mode,
                policy_version=AUTHORIZATION_POLICY_VERSION,
                role=role,
                payload=submission.payload,
                correlation_id=scope.correlation_id,
                idempotency_key=submission.idempotency_key,
            )
        )
        self._wake.set()
        return job  # type: ignore[no-any-return]

    async def _authorize(self, job: Job) -> None:
        job.envelope().revalidate()
        # Whether access is checked is the install's setting, never the stored job's:
        # a job left from a local install is checked like any other on a hosted one.
        if self._local_mode or self._tenancy is None:
            return
        try:
            await self._tenancy.require_project(
                job.envelope().scope(), job.workspace_id, job.project_id, Permission.PROJECT_WRITE
            )
        except DomainError as error:
            raise JobAuthorizationError("access was withdrawn") from error

    async def _branch(self, job: Job) -> str | None:
        branch_id = job.payload.get("branch_id")
        if not branch_id:
            return None
        branch = await self._journal.get_branch(
            workspace_id=job.workspace_id, project_id=job.project_id, branch_id=branch_id
        )
        if branch is None:
            raise NotFoundError("That what-if is no longer there.")
        return None if branch.is_root else branch.id

    async def run_once(self) -> Job | None:
        """Claim and run one job. Returns it as it finished, or None if there was none."""
        job = await self._store.claim(owner=self._owner)
        if job is None:
            return None
        handler = self._handlers.get(job.kind)
        context = JobContext(self._store, job, self._owner)
        owner = self._owner
        try:
            if handler is None:
                raise ValidationError("That kind of work cannot be done here.")
            await self._authorize(job)
            with working_in(await self._branch(job)):
                result = await handler(context)
            finished = await self._store.finish(
                job, owner=owner, status=JobStatus.SUCCEEDED, result=result
            )
        except JobCancelled:
            finished = await self._store.finish(job, owner=owner, status=JobStatus.CANCELLED)
        except LeaseLost:
            # Another worker holds the job now (or it was given up): its ending is theirs.
            log_event(logger, "job.lease_lost", job_kind=job.kind)
            current = await self._store.get(
                workspace_id=job.workspace_id, project_id=job.project_id, job_id=job.id
            )
            return current or job  # type: ignore[no-any-return]
        except asyncio.CancelledError:
            # The app is stopping: put the job back so the next start picks it up rather
            # than leaving it held until its lease runs out.
            try:
                await asyncio.shield(self._store.requeue(job, owner=owner))
            except Exception as error:  # noqa: BLE001 - the stop itself must go on
                log_event(
                    logger,
                    "job.requeue_failed",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )
            raise
        except BaseException as error:  # noqa: BLE001 - every failure is recorded, never lost
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            code, message = "internal", "Something went wrong. Nothing was saved from this run."
            for kind, known_code, known_message in _FAILURES:
                if isinstance(error, kind):
                    code, message = known_code, known_message
                    break
            else:
                if isinstance(error, DomainError):
                    code, message = "refused", str(error)[:300]
            if code == "internal":
                # The kind of error only: its message may quote the author's words.
                log_event(
                    logger,
                    "job.failed.internal",
                    level=logging.WARNING,
                    job_kind=job.kind,
                    error_type=type(error).__name__,
                )
            finished = await self._store.finish(
                job, owner=owner, status=JobStatus.FAILED, error_code=code, error_message=message
            )
        log_event(
            logger,
            "job.finished",
            job_kind=job.kind,
            status=finished.status.value,
            error_code=finished.error_code,
        )
        return finished  # type: ignore[no-any-return]

    async def run_pending(self, limit: int = 20) -> list[Job]:
        """Run waiting jobs now, one after another. Used by tests and the command line."""
        done: list[Job] = []
        for _ in range(limit):
            finished = await self.run_once()
            if finished is None:
                break
            done.append(finished)
        return done

    async def _loop(self) -> None:
        while True:
            try:
                finished = await self.run_once()
            except Exception as error:  # noqa: BLE001 - the loop must survive a bad poll
                log_event(
                    logger, "job.loop.error", level=logging.WARNING, error_type=type(error).__name__
                )
                finished = None
            if finished is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=POLL_SECONDS)
                except TimeoutError:
                    pass

    @property
    def running(self) -> bool:
        """Whether the background loop is going (it never ends on its own)."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is None and self.autostart:
            self._task = asyncio.create_task(self._loop(), name="job-runner")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


__all__ = ["JobContext", "JobRunner", "JobSubmission"]
