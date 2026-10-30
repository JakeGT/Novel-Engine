"""Audits the author opted into running on a schedule (CON-011).

The author sets up a schedule from the audit dialog: a part of the story, the passes,
every how many days, and the most one run may use. The job runner's loop looks for
schedules that are due about once a minute, between jobs when it is busy (``tick``),
and starts at most ``CLAIM_LIMIT`` each time; each due schedule is claimed in one
conditional update, so two servers never start the same run, and a run claimed again
after a server stopped gives back the audit it already started.

A run is the author's own audit, started on their behalf in the line of the story the
schedule belongs to, and it goes through every check a request would: their access to
the story (lost access turns the schedule off), the plan part and the what-if still
being there, the most they set for one run (which stands for their confirmation, since
nobody is there to confirm), the story's confirmation threshold above that, whether a
model is set up, their allowance, and the three-jobs limit. Once it runs, the most they
set is a hard limit: a scene planned again above it stops the audit, keeping what it
finished. A run whose scenes are all unchanged since the last audit that succeeded is
skipped and spends nothing; one that failed or stopped part of the way is run again.
What each run came to is kept on the schedule in a fixed word and shown as a sentence.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.audit_plans import (
    MAX_EVERY_DAYS,
    MAX_SCHEDULE_TOKENS,
    MAX_SCHEDULES_PER_STORY,
    MIN_EVERY_DAYS,
    AuditSchedule,
)
from backend.domain.branching import working_in
from backend.domain.errors import DomainError, NotFoundError, RateLimitedError, ValidationError
from backend.domain.identity import AccessScope, AuthMode, utcnow
from backend.domain.jobs import MAX_ACTIVE_JOBS, JobStatus
from backend.domain.tenancy import Permission
from backend.domain.usage import UsageLimitExceeded
from backend.domain.usage_controls import NeedsConfirmation

logger = logging.getLogger(__name__)

# How often the loop looks for schedules that are due.
TICK_SECONDS = 60.0
# A claimed run that is never recorded (the server stopped) comes due again after this.
HOLD_MINUTES = 30
# A run that could not start for a passing reason is tried again this soon.
RETRY_MINUTES = 60
# The most schedules one look starts, so the loop gets back to the waiting jobs soon;
# any more that are due are started on the next look, a minute later.
CLAIM_LIMIT = 5


def inputs_digest(passes: tuple[str, ...], scenes: list[tuple[str, str | None]]) -> str:
    """A digest of what a run would read: the passes and each scene's exact version."""
    body = json.dumps({"passes": sorted(passes), "scenes": scenes}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class AuditScheduleService:
    def __init__(self, *, audits: Any, store: Any, clock: Any = utcnow) -> None:
        self._audits = audits
        self._store = store
        self._clock = clock

    @property
    def _studio(self) -> Any:
        return self._audits._studio

    def register(self, runner: Any) -> None:
        runner.every(TICK_SECONDS, self.tick)

    # ------------------------------------------------------------- the author's

    async def list(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[AuditSchedule]:
        """The schedules of the line of the story being worked in."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        line = await studio._draft_branch(workspace_id, project_id)
        return [
            item
            for item in await self._store.all_for(workspace_id=workspace_id, project_id=project_id)
            if item.branch_id == line
        ]

    async def save(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        plan_id: str,
        passes: Any,
        every_days: int,
        max_tokens: int,
        confirmed_tokens: int | None = None,
    ) -> AuditSchedule:
        """Set up (or change) the schedule for a part of the story in this line."""
        from backend.services.story_audit import _passes

        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not MIN_EVERY_DAYS <= every_days <= MAX_EVERY_DAYS:
            raise ValidationError(
                f"Choose every {MIN_EVERY_DAYS} to {MAX_EVERY_DAYS} days.",
                field_errors={"every_days": "out of range"},
            )
        if not 0 <= max_tokens <= MAX_SCHEDULE_TOKENS:
            raise ValidationError(
                f"The most a run may use is between 0 and {MAX_SCHEDULE_TOKENS:,} tokens.",
                field_errors={"max_tokens": "out of range"},
            )
        chosen = _passes(passes)
        if chosen and max_tokens == 0:
            raise ValidationError(
                "Say the most each run may use, or choose only the checks without the assistant.",
                field_errors={"max_tokens": "required"},
            )
        await self._audits._part(scope, workspace_id, project_id, plan_id)
        if chosen:
            # Setting up the most a run may use stands for confirming it each time, so it
            # is confirmed now, like any request of that size.
            await studio.confirm(
                workspace_id,
                project_id,
                estimated_tokens=max_tokens,
                confirmed_tokens=confirmed_tokens,
                doing="Auditing on a schedule",
            )
        line = await studio._draft_branch(workspace_id, project_id)
        existing = [
            item
            for item in await self._store.all_for(workspace_id=workspace_id, project_id=project_id)
        ]
        same = next(
            (item for item in existing if item.branch_id == line and item.plan_id == plan_id), None
        )
        if same is None and len(existing) >= MAX_SCHEDULES_PER_STORY:
            raise ValidationError(
                f"A story can have at most {MAX_SCHEDULES_PER_STORY} scheduled audits. Stop one first."
            )
        now = self._clock()
        saved = await self._store.save(
            AuditSchedule(
                workspace_id=workspace_id,
                project_id=project_id,
                id=same.id if same is not None else f"sch_{uuid7()}",
                branch_id=line,
                plan_id=plan_id,
                passes=tuple(item.value for item in chosen),
                every_days=every_days,
                max_tokens=max_tokens,
                actor_user_id=scope.actor_user_id,
                auth_mode=str(getattr(scope.auth_mode, "value", scope.auth_mode)),
                next_run_at=now + timedelta(days=every_days),
                enabled=True,
            )
        )
        log_event(logger, "audit.schedule_saved", every_days=every_days, passes=len(chosen))
        return saved  # type: ignore[no-any-return]

    async def remove(
        self, scope: AccessScope, workspace_id: str, project_id: str, schedule_id: str
    ) -> None:
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not await self._store.delete(
            workspace_id=workspace_id, project_id=project_id, schedule_id=schedule_id
        ):
            raise NotFoundError("That schedule was not found.")

    # ------------------------------------------------------------------ running

    async def tick(self) -> int:
        """Start every schedule that is due. Returns how many were looked at."""
        now = self._clock()
        due = await self._store.claim_due(
            now=now, hold_until=now + timedelta(minutes=HOLD_MINUTES), limit=CLAIM_LIMIT
        )
        for schedule in due:
            try:
                outcome = await self.run(schedule, now=now)
            except Exception as error:  # noqa: BLE001 - one schedule never stops the others
                log_event(
                    logger,
                    "audit.schedule_failed",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )
                await self._record(schedule, now, "not_started")
                continue
            log_event(logger, "audit.schedule_ran", outcome=outcome)
        return len(due)

    async def _record(
        self,
        schedule: AuditSchedule,
        now: datetime,
        outcome: str,
        *,
        job_id: str | None = None,
        inputs: str | None = None,
    ) -> str:
        off = outcome in ("not_allowed", "gone")
        soon = outcome in ("job_cap", "not_started")
        await self._store.record_run(
            workspace_id=schedule.workspace_id,
            project_id=schedule.project_id,
            schedule_id=schedule.id,
            ran_at=now,
            outcome=outcome,
            next_run_at=now
            + (timedelta(minutes=RETRY_MINUTES) if soon else timedelta(days=schedule.every_days)),
            job_id=job_id,
            # Only ever what an audit that succeeded read (``_settled``).
            inputs=inputs if inputs is not None else schedule.last_inputs,
            enabled=not off,
        )
        return outcome

    async def _settled(self, schedule: AuditSchedule) -> tuple[AuditSchedule, str | None]:
        """The schedule with ``last_inputs`` as the last audit that *succeeded* read, and
        what the last audit reads while it is still waiting or under way. A run that failed
        or stopped part of the way leaves ``last_inputs`` as it was, so the next run audits
        again even when nothing was written meanwhile."""
        if not schedule.last_job_id:
            return schedule, None
        job = await self._studio._jobs.get(
            workspace_id=schedule.workspace_id,
            project_id=schedule.project_id,
            job_id=schedule.last_job_id,
        )
        if job is None or (job.status.finished and job.status is not JobStatus.SUCCEEDED):
            return schedule, None
        read = inputs_digest(
            schedule.passes,
            [
                (str(entry.get("plan_id")), entry.get("version_id"))
                for entry in job.payload.get("scenes") or []
            ],
        )
        if not job.status.finished:
            return schedule, read
        return replace(schedule, last_inputs=read), None

    async def run(self, schedule: AuditSchedule, *, now: datetime | None = None) -> str:
        """One run of a schedule, as its author, in its line of the story."""
        now = now or self._clock()
        studio = self._studio
        workspace_id, project_id = schedule.workspace_id, schedule.project_id
        scope = AccessScope(
            actor_user_id=schedule.actor_user_id,
            auth_mode=AuthMode(schedule.auth_mode),
            correlation_id=f"schedule:{schedule.id}",
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if schedule.branch_id is not None:
            branch = await studio._journal.get_branch(
                workspace_id=workspace_id, project_id=project_id, branch_id=schedule.branch_id
            )
            if branch is None or getattr(branch, "status", "active") != "active":
                return await self._record(schedule, now, "gone")
        schedule, under_way = await self._settled(schedule)
        with working_in(schedule.branch_id):
            try:
                await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
            except DomainError:
                return await self._record(schedule, now, "not_allowed")
            try:
                found = await self._audits.estimate(
                    scope, workspace_id, project_id, schedule.plan_id, list(schedule.passes)
                )
            except (NotFoundError, ValidationError):
                return await self._record(schedule, now, "gone")
            written = found.written
            if not written:
                return await self._record(schedule, now, "nothing_written")
            from backend.services.story_audit import MAX_AUDIT_SCENES

            if len(written) > MAX_AUDIT_SCENES:
                return await self._record(schedule, now, "too_many_scenes")
            digest = inputs_digest(
                schedule.passes,
                [
                    (entry.scene.id, entry.version.id if entry.version else None)
                    for entry in written
                ],
            )
            if digest in (schedule.last_inputs, under_way):
                return await self._record(schedule, now, "unchanged")
            total = found.total
            if found.passes:
                if total > schedule.max_tokens:
                    return await self._record(schedule, now, "over_most")
                if not await studio._provider_ready(scope, workspace_id):
                    return await self._record(schedule, now, "no_provider")
            active = await studio._jobs.count_active(
                workspace_id=workspace_id, project_id=project_id, actor_user_id=scope.actor_user_id
            )
            if active >= MAX_ACTIVE_JOBS:
                return await self._record(schedule, now, "job_cap")
            try:
                job = await self._audits.start(
                    scope,
                    workspace_id,
                    project_id,
                    schedule.plan_id,
                    passes=list(schedule.passes),
                    # The most the author set for one run is what they confirmed.
                    confirmed_tokens=schedule.max_tokens if found.passes else None,
                    # One run per period: the period after the last run recorded. A run
                    # claimed again after the server stopped before recording it gives
                    # back the audit it already started rather than paying for another.
                    idempotency_key=f"schedule:{schedule.id}:{_period(schedule)}",
                    schedule_id=schedule.id,
                    found=found,
                )
            except NeedsConfirmation:
                return await self._record(schedule, now, "needs_confirmation")
            except UsageLimitExceeded:
                return await self._record(schedule, now, "allowance")
            except RateLimitedError:
                return await self._record(schedule, now, "job_cap")
            except DomainError:
                return await self._record(schedule, now, "not_started")
        # What it reads is kept once the audit succeeds (``_settled``), never before.
        return await self._record(schedule, now, "started", job_id=job.id)


def _period(schedule: AuditSchedule) -> str:
    """Which run of a schedule this is: the one after the last run recorded."""
    last = schedule.last_run_at
    return last.isoformat() if last is not None else "first"


__all__ = ["CLAIM_LIMIT", "AuditScheduleService", "TICK_SECONDS", "inputs_digest"]
