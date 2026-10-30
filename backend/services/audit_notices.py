"""Telling the author when a scheduled audit finishes (CON-011).

The job runner calls ``job_finished`` once a job ends, however it ended. For an audit a
schedule started, a notice is kept for the author it ran as: how many scenes it read,
how many notes the assistant found and how many findings the checks without it found in
those scenes, and what it used. The home page of the story and the audit's own page show
it until the author marks it read. One notice per audit: a finish recorded twice (a
worker that lost its lease) keeps the first. An audit stopped while it was still waiting
ends without ever reaching the runner, so stopping it tells this service itself. Nothing here is the story's words; the part
of the plan is named when the notice is read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.branching import working_in
from backend.domain.identity import AccessScope, utcnow
from backend.domain.jobs import Job, JobStatus
from backend.domain.notices import MAX_LISTED, Notice, notice_sentence
from backend.domain.studio import AUDIT_JOB
from backend.domain.tenancy import Permission

logger = logging.getLogger(__name__)

_ENDED = {
    JobStatus.SUCCEEDED: "succeeded",
    JobStatus.FAILED: "failed",
    JobStatus.CANCELLED: "cancelled",
}


@dataclass(frozen=True)
class NoticeReading:
    notice: Notice
    # What the audit covered, named now: "“Act One”" or "the whole story".
    part: str
    sentence: str
    # The what-if it ran in, by name; None for the main story.
    what_if: str | None = None


class AuditNoticeService:
    def __init__(self, *, store: Any, audits: Any, clock: Any = utcnow) -> None:
        self._store = store
        self._audits = audits
        self._clock = clock

    @property
    def _studio(self) -> Any:
        return self._audits._studio

    def register(self, runner: Any) -> None:
        on_finished = getattr(runner, "on_finished", None)
        if on_finished is not None:
            on_finished(self.job_finished)
        self._told_when_stopped_waiting()

    def _told_when_stopped_waiting(self) -> None:
        """Wrap the studio's ``cancel`` so a job it ends at once (one still waiting, which
        the runner never sees) is told of here as well. A running job is told of by the
        runner when it stops; a notice kept twice keeps the first."""
        studio = self._studio
        cancel = getattr(studio, "cancel", None)
        if cancel is None or getattr(cancel, "tells_notices", False):
            return

        async def cancel_then_tell(
            scope: AccessScope, workspace_id: str, project_id: str, job_id: str
        ) -> Job:
            job: Job = await cancel(scope, workspace_id, project_id, job_id)
            if job.status is JobStatus.CANCELLED:
                try:
                    await self.job_finished(job)
                except Exception as error:  # noqa: BLE001 - the stop itself has happened
                    log_event(
                        logger,
                        "audit.notice_failed",
                        level=logging.WARNING,
                        error_type=type(error).__name__,
                    )
            return job

        cancel_then_tell.tells_notices = True  # type: ignore[attr-defined]
        studio.cancel = cancel_then_tell

    # ------------------------------------------------------------------- recording

    async def job_finished(self, job: Job) -> None:
        """Keep a notice when a scheduled audit ends. Other work is left alone."""
        if job.kind != AUDIT_JOB or not job.payload.get("schedule_id"):
            return
        status = _ENDED.get(job.status)
        if status is None:
            return
        scenes = [
            entry
            for entry in (job.result or {}).get("scenes", [])
            if isinstance(entry, dict) and entry.get("status") in ("checked", "partial")
        ]
        notes = sum(int(entry.get("findings", 0) or 0) for entry in scenes)
        checks = await self._checks_found(job)
        added = await self._store.add(
            Notice(
                workspace_id=job.workspace_id,
                project_id=job.project_id,
                id=f"ntc_{uuid7()}",
                user_id=job.actor_user_id,
                kind="audit_finished",
                job_id=job.id,
                status=status,  # type: ignore[arg-type]
                branch_id=job.payload.get("branch_id"),
                schedule_id=str(job.payload.get("schedule_id")),
                plan_id=str(job.payload.get("plan_id") or ""),
                scenes=len(scenes),
                notes=notes,
                checks=checks,
                prompt_tokens=job.prompt_tokens,
                completion_tokens=job.completion_tokens,
                cost_usd=job.provider_cost_usd,
            )
        )
        if added:
            log_event(logger, "audit.notice_kept", status=status, scenes=len(scenes))

    async def _checks_found(self, job: Job) -> int:
        """How many findings the checks without the assistant have in the scenes read; 0
        when they cannot be worked out now (they are on the audit's page either way)."""
        try:
            line = job.payload.get("branch_id")
            if line:
                branch = await self._studio._journal.get_branch(
                    workspace_id=job.workspace_id, project_id=job.project_id, branch_id=line
                )
                line = None if branch is None or branch.is_root else branch.id
            with working_in(line):
                report = await self._audits.report(
                    job.envelope().scope(), job.workspace_id, job.project_id, job.id
                )
        except Exception as error:  # noqa: BLE001 - the notice is kept without them
            log_event(logger, "audit.notice_checks_skipped", error_type=type(error).__name__)
            return 0
        return sum(len(scene.checks) + len(scene.story_checks) for scene in report.scenes)

    # --------------------------------------------------------------------- reading

    async def list(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        job_id: str | None = None,
        unseen_only: bool = False,
    ) -> list[NoticeReading]:
        """The author's own notices in this story, newest first, in every line of it."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        found = await self._store.for_user(
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=scope.actor_user_id,
            limit=MAX_LISTED,
            job_id=job_id,
            # Filtered before the limit: unread ones past the newest read ones still show.
            unseen_only=unseen_only,
        )
        readings: list[NoticeReading] = []
        names: dict[str | None, str | None] = {}
        for notice in found:
            what_if = await self._what_if(workspace_id, project_id, notice.branch_id, names)
            part = await self._part(workspace_id, project_id, notice)
            readings.append(
                NoticeReading(
                    notice=notice,
                    part=part,
                    sentence=notice_sentence(notice, part),
                    what_if=what_if,
                )
            )
        return readings

    async def _what_if(
        self,
        workspace_id: str,
        project_id: str,
        branch_id: str | None,
        names: dict[str | None, str | None],
    ) -> str | None:
        if branch_id in names:
            return names[branch_id]
        name: str | None = None
        if branch_id:
            branch = await self._studio._journal.get_branch(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
            if branch is not None and not branch.is_root:
                name = str(getattr(branch, "name", "") or "a what-if")
        names[branch_id] = name
        return name

    async def _part(self, workspace_id: str, project_id: str, notice: Notice) -> str:
        from backend.services.story_audit import WHAT_IF_CHANGES, WHOLE_PLAN

        if notice.plan_id == WHOLE_PLAN:
            return "the whole story"
        if notice.plan_id == WHAT_IF_CHANGES:
            return "the scenes written differently in a what-if"
        with working_in(notice.branch_id):
            item = await self._studio._reader.get_plan_item(
                workspace_id=workspace_id, project_id=project_id, plan_id=notice.plan_id
            )
        if item is None or not item.is_active:
            return "a part of the plan no longer there"
        return f"“{item.title}”"

    async def mark_seen(
        self, scope: AccessScope, workspace_id: str, project_id: str, notice_id: str
    ) -> bool:
        await self._studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return bool(
            await self._store.mark_seen(
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=scope.actor_user_id,
                notice_id=notice_id,
                at=self._clock(),
            )
        )


__all__ = ["AuditNoticeService", "NoticeReading"]
