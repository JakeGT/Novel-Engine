"""Checks kept as runs, and what they come to now (CON-001).

A run is recorded when the studio's checks that need no model run on a saved or kept
version (one per version and story version), when a check with the assistant reads the
scene, and when an audit starts reading. It is read back judged against the story as it
is now: each finding open, dismissed (with the author's reason) or fixed (its words or
the story changed), and each scene marked out of date when its words, or what it names in
the story bible, changed since. Nothing is stored about that, so it cannot drift; nothing
here ever blocks a change or calls a model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.consistency_runs import (
    MAX_CHANGES,
    MAX_HISTORY,
    MODEL_FREE,
    ConsistencyRun,
    FindingStatus,
    FixedBy,
    RecordedCheck,
    RunInput,
    RunKind,
    RunScope,
    ScenePart,
    Staleness,
    judge_check,
    judge_note,
    story_change_touches,
)
from backend.domain.dismissals import FindingDismissal
from backend.domain.drafting import DraftFinding, Severity
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.jobs import Job
from backend.domain.knowledge import SCENE_TIME_KEY
from backend.domain.studio import AssistantFinding
from backend.domain.tenancy import Permission

logger = logging.getLogger(__name__)

MAX_QUOTED = 400


@dataclass(frozen=True)
class FindingView:
    """One finding of a run, and what it comes to now."""

    plan_id: str
    # "check": a check that needs no model; "note": the assistant's note.
    source: Literal["check", "note"]
    code: str
    severity: str
    message: str
    quoted: str
    status: FindingStatus
    fixed_by: FixedBy | None = None
    dismissal: FindingDismissal | None = None
    check_pass: str | None = None
    # Still open, but the scene changed since the run: it may no longer hold.
    stale: bool = False


@dataclass
class RunView:
    run: ConsistencyRun
    title: str
    # The run's job status, or "done" for the checks that need no model.
    status: str
    scenes: list[ScenePart] = field(default_factory=list)
    findings: list[FindingView] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return any(part.staleness.stale for part in self.scenes)


@dataclass
class _Now:
    """A scene as it is now: its words and what the checks find in them."""

    scene: PlanItem | None
    source_id: str | None = None
    version_id: str | None = None
    content: str | None = None
    found: set[str] = field(default_factory=set)
    dismissed: dict[str, FindingDismissal] = field(default_factory=dict)


def _quoted(content: str, start: int | None, end: int | None) -> str:
    if start is None or end is None or not 0 <= start < end <= len(content):
        return ""
    return content[start:end][:MAX_QUOTED]


def _recorded(plan_id: str, found: list[DraftFinding]) -> tuple[RecordedCheck, ...]:
    return tuple(
        RecordedCheck(
            plan_id=plan_id,
            code=item.code,
            severity=item.severity.value,
            message=item.message,
            start=item.start,
            end=item.end,
            subject=item.subject,
        )
        for item in found
    )


def _as_finding(check: RecordedCheck) -> DraftFinding:
    try:
        severity = Severity(check.severity)
    except ValueError:
        severity = Severity.NOTE
    return DraftFinding(
        check.code, severity, check.message, check.start, check.end, subject=check.subject
    )


class ConsistencyRunService:
    """Records runs and reads them back judged; built on the studio (``StudioService``)."""

    def __init__(self, *, store: Any, studio: Any) -> None:
        self._store = store
        self._studio = studio

    # ------------------------------------------------------------------ helpers

    async def _line(self, workspace_id: str, project_id: str) -> tuple[str, int]:
        branch = await self._studio._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        head = await self._studio._reader.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        return str(branch.id), int(head)

    async def _checks_of(
        self, workspace_id: str, project_id: str, scene: PlanItem, source_id: str, content: str
    ) -> list[DraftFinding]:
        studio = self._studio
        brief = await studio.brief(workspace_id, project_id, scene)
        return await studio._check(  # type: ignore[no-any-return]
            workspace_id, project_id, source_id, content, brief, scene_id=scene.id
        )

    # ---------------------------------------------------------------- recording

    async def record_checks(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        asked: bool = False,
    ) -> ConsistencyRun | None:
        """Keep what the checks that need no model find in the scene's current text, once
        per version of the text and of the story. ``asked`` when the author asked for it,
        so nothing written is said plainly rather than skipped."""
        studio = self._studio
        if asked:
            await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        scene = await studio._scene(scope, workspace_id, project_id, plan_id)
        text = await studio._text(workspace_id, project_id, plan_id)
        if text is None:
            if asked:
                raise ValidationError("There is nothing written for this scene to check yet.")
            return None
        _, current, _ = await studio._sources.version(workspace_id, project_id, text.id)
        branch_id, head = await self._line(workspace_id, project_id)
        latest = await self._store.recent(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            plan_id=plan_id,
            kind="checks",
            limit=1,
        )
        if (
            latest
            and latest[0].story_revision == head
            and latest[0].inputs
            and latest[0].inputs[0].version_id == current.id
        ):
            return latest[0]  # type: ignore[no-any-return]
        found = await self._checks_of(workspace_id, project_id, scene, text.id, current.content)
        run = ConsistencyRun(
            workspace_id=workspace_id,
            project_id=project_id,
            id=str(uuid7()),
            branch_id=branch_id,
            kind="checks",
            scope="scene",
            plan_id=plan_id,
            story_revision=head,
            inputs=(RunInput(plan_id=plan_id, source_id=text.id, version_id=current.id),),
            passes=(MODEL_FREE,),
            findings=_recorded(plan_id, found),
            created_by=scope.actor_user_id,
        )
        await self._store.add(run)
        log_event(logger, "checks.run_recorded", kind="checks", findings=len(found))
        return run

    async def record_assistant(
        self,
        job: Job,
        *,
        plan_id: str,
        content: str,
        source_id: str | None,
        version_id: str | None,
        draft_id: str | None,
        passes: tuple[str, ...],
    ) -> None:
        """A check with the assistant, as it reads the scene: once per job, with what the
        checks that need no model find in the same words."""
        workspace_id, project_id = job.workspace_id, job.project_id
        studio = self._studio
        scene = await studio._reader.get_plan_item(
            workspace_id=workspace_id, project_id=project_id, plan_id=plan_id
        )
        if scene is None:
            return
        branch_id, head = await self._line(workspace_id, project_id)
        found = await self._checks_of(workspace_id, project_id, scene, source_id or "", content)
        added = await self._store.add(
            ConsistencyRun(
                workspace_id=workspace_id,
                project_id=project_id,
                id=str(uuid7()),
                branch_id=branch_id,
                kind="assistant",
                scope="scene",
                plan_id=plan_id,
                story_revision=head,
                inputs=(
                    RunInput(
                        plan_id=plan_id,
                        source_id=source_id,
                        version_id=version_id,
                        draft_id=draft_id,
                    ),
                ),
                passes=(MODEL_FREE, *passes),
                findings=_recorded(plan_id, found),
                job_id=job.id,
                created_by=job.actor_user_id,
            )
        )
        if added:
            log_event(logger, "checks.run_recorded", kind="assistant", passes=len(passes))

    async def record_audit(self, job: Job) -> None:
        """An audit, as it starts reading: once per job, the exact versions it reads and
        what the checks that need no model find in each."""
        workspace_id, project_id = job.workspace_id, job.project_id
        if await self._store.for_job(
            workspace_id=workspace_id, project_id=project_id, job_id=job.id
        ):
            return
        studio = self._studio
        payload = job.payload
        plan_id = str(payload.get("plan_id") or "")
        scope_kind: RunScope = "story"
        if plan_id:
            part = await studio._reader.get_plan_item(
                workspace_id=workspace_id, project_id=project_id, plan_id=plan_id
            )
            scope_kind = "scene" if part is not None and part.kind is PlanKind.SCENE else "part"
        inputs: list[RunInput] = []
        found: list[RecordedCheck] = []
        for entry in payload.get("scenes") or []:
            item = RunInput(
                plan_id=str(entry.get("plan_id") or ""),
                source_id=entry.get("source_id"),
                version_id=entry.get("version_id"),
            )
            inputs.append(item)
            scene = await studio._reader.get_plan_item(
                workspace_id=workspace_id, project_id=project_id, plan_id=item.plan_id
            )
            content = await self._then(workspace_id, project_id, item)
            if scene is not None and scene.is_active and content is not None:
                found += _recorded(
                    item.plan_id,
                    await self._checks_of(
                        workspace_id, project_id, scene, item.source_id or "", content
                    ),
                )
        branch_id, head = await self._line(workspace_id, project_id)
        await self._store.add(
            ConsistencyRun(
                workspace_id=workspace_id,
                project_id=project_id,
                id=str(uuid7()),
                branch_id=branch_id,
                kind="audit",
                scope=scope_kind,
                plan_id=plan_id or None,
                story_revision=head,
                inputs=tuple(inputs),
                passes=(MODEL_FREE, *(str(p) for p in payload.get("passes") or [])),
                findings=tuple(found),
                job_id=job.id,
                created_by=job.actor_user_id,
            )
        )
        log_event(logger, "checks.run_recorded", kind="audit", scenes=len(inputs))

    # ------------------------------------------------------------------ reading

    async def history(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> list[RunView]:
        """The runs that read this scene, newest first, each judged for this scene."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        await studio._scene(scope, workspace_id, project_id, plan_id)
        branch_id, _ = await self._line(workspace_id, project_id)
        runs = await self._store.recent(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            plan_id=plan_id,
            limit=MAX_HISTORY,
        )
        cache: dict[str, _Now] = {}
        return [
            await self._judge(workspace_id, project_id, run, only=plan_id, cache=cache)
            for run in runs
        ]

    async def get(
        self, scope: AccessScope, workspace_id: str, project_id: str, run_id: str
    ) -> RunView:
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        run = await self._store.get(workspace_id=workspace_id, project_id=project_id, run_id=run_id)
        if run is None or run.branch_id != (await self._line(workspace_id, project_id))[0]:
            raise NotFoundError("That check was not found.")
        return await self._judge(workspace_id, project_id, run, only=None, cache={})

    async def for_job(
        self, scope: AccessScope, workspace_id: str, project_id: str, job_id: str
    ) -> RunView:
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        run = await self._store.for_job(
            workspace_id=workspace_id, project_id=project_id, job_id=job_id
        )
        if run is None or run.branch_id != (await self._line(workspace_id, project_id))[0]:
            raise NotFoundError("That check has no record yet.")
        return await self._judge(workspace_id, project_id, run, only=None, cache={})

    async def _now(self, workspace_id: str, project_id: str, plan_id: str) -> _Now:
        studio = self._studio
        scene = await studio._reader.get_plan_item(
            workspace_id=workspace_id, project_id=project_id, plan_id=plan_id
        )
        if scene is None or not scene.is_active:
            return _Now(scene=None)
        now = _Now(scene=scene)
        text = await studio._text(workspace_id, project_id, plan_id)
        if text is not None:
            _, current, _ = await studio._sources.version(workspace_id, project_id, text.id)
            now.source_id, now.version_id, now.content = text.id, current.id, current.content
            found = await self._checks_of(workspace_id, project_id, scene, text.id, current.content)
            from backend.services.studio_service import check_key

            now.found = {check_key(current.content, item) for item in found}
        branch_id, _ = await self._line(workspace_id, project_id)
        moment = datetime.now(UTC)
        standing = await studio._store.dismissals(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, plan_id=plan_id
        )
        now.dismissed = {item.fingerprint: item for item in standing if item.applies(moment)}
        return now

    async def _then(self, workspace_id: str, project_id: str, item: RunInput) -> str | None:
        """The words the run read, if they are still kept."""
        studio = self._studio
        if item.draft_id:
            draft = await studio._store.get_draft(
                workspace_id=workspace_id, project_id=project_id, draft_id=item.draft_id
            )
            return draft.content if draft is not None else None
        if not item.source_id or not item.version_id:
            return None
        try:
            _, version, _ = await studio._sources.version(
                workspace_id, project_id, item.source_id, item.version_id
            )
        except NotFoundError:
            return None
        return str(version.content)

    async def _changed(
        self, workspace_id: str, project_id: str, run: ConsistencyRun
    ) -> list[Any] | None:
        """The story's changes since the run, or None when there are too many to look at."""
        changes = await self._studio._reader.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            min_revision=run.story_revision,
            limit=MAX_CHANGES + 1,
        )
        return None if len(changes) > MAX_CHANGES else list(changes)

    async def _rules_changed(self, workspace_id: str, project_id: str, run: ConsistencyRun) -> bool:
        """Whether one of the author's rules was written or changed since the run: a rule
        is about the whole story, so every scene's findings may hold differently."""
        rules = getattr(getattr(self._studio, "_context", None), "_rules", None)
        if rules is None or run.created_at is None:
            return False
        listed = await rules.list_rules(workspace_id=workspace_id, project_id=project_id)
        return any(
            (
                getattr(rule, "updated_at", None)
                or getattr(rule, "created_at", None)
                or run.created_at
            )
            > run.created_at
            for rule in listed
        )

    async def _story_touches(
        self,
        workspace_id: str,
        project_id: str,
        changes: list[Any] | None,
        scene: PlanItem | None,
        plan_id: str,
        records: dict[str, set[str]],
        *,
        rules_changed: bool = False,
    ) -> bool:
        if changes is None or rules_changed:
            return True
        about = {plan_id, *(ref.entity_id for ref in (scene.refs if scene else []))}
        for change in changes:
            if story_change_touches(change.change, change.record_id, about):
                return True
            lookup = (
                (change.record_type in ("fact", "belief") and "proposition" not in change.change)
                or (
                    # Amending or removing an event names only the event.
                    change.record_type == "event" and "participants" not in change.change
                )
                or (
                    # Another scene's time or place moving can change what this one can be.
                    change.record_type == "plan_item"
                    and SCENE_TIME_KEY in (change.change.get("details") or {})
                )
            )
            if not lookup:
                continue
            # Ending or amending a record names only the record: look it up.
            if change.record_id not in records:
                records[change.record_id] = await self._named_by(
                    workspace_id, project_id, change.record_type, change.record_id
                )
            if records[change.record_id] & about:
                return True
        return False

    async def _named_by(
        self, workspace_id: str, project_id: str, record_type: str, record_id: str
    ) -> set[str]:
        """The entries a stored fact, belief, event or plan item names."""
        reader = self._studio._reader
        named: set[str] = set()
        if record_type == "fact":
            fact = await reader.get_fact(
                workspace_id=workspace_id, project_id=project_id, fact_id=record_id
            )
            if fact is not None:
                named |= {fact.proposition.subject_id}
                target = getattr(fact.proposition.object, "entity_id", None)
                named |= {target} if target else set()
        elif record_type == "belief":
            belief = await reader.get_belief(
                workspace_id=workspace_id, project_id=project_id, belief_id=record_id
            )
            if belief is not None:
                named |= {belief.holder_entity_id, belief.proposition.subject_id}
        elif record_type == "event":
            event = await reader.get_event(
                workspace_id=workspace_id, project_id=project_id, event_id=record_id
            )
            if event is not None:
                named |= {p.entity_id for p in event.participants} | set(event.location_ids)
                named |= {event.scene_id} if getattr(event, "scene_id", None) else set()
        elif record_type == "plan_item":
            item = await reader.get_plan_item(
                workspace_id=workspace_id, project_id=project_id, plan_id=record_id
            )
            if item is not None:
                named |= {ref.entity_id for ref in item.refs}
        return named

    async def _status(self, workspace_id: str, project_id: str, run: ConsistencyRun) -> str:
        if run.job_id is None:
            return "done"
        job = await self._studio._jobs.get(
            workspace_id=workspace_id, project_id=project_id, job_id=run.job_id
        )
        return "done" if job is None else str(job.status.value)

    async def _title(self, workspace_id: str, project_id: str, run: ConsistencyRun) -> str:
        if run.plan_id is None:
            return "The whole story"
        item = await self._studio._reader.get_plan_item(
            workspace_id=workspace_id, project_id=project_id, plan_id=run.plan_id
        )
        return str(item.title) if item is not None else "A part of the plan no longer there"

    async def _judge(
        self,
        workspace_id: str,
        project_id: str,
        run: ConsistencyRun,
        *,
        only: str | None,
        cache: dict[str, _Now],
    ) -> RunView:
        from backend.services.studio_service import check_key, note_key

        view = RunView(
            run=run,
            title=await self._title(workspace_id, project_id, run),
            status=await self._status(workspace_id, project_id, run),
        )
        notes: list[AssistantFinding] = []
        if run.job_id is not None:
            notes = await self._studio._store.findings_for_job(
                workspace_id=workspace_id, project_id=project_id, job_id=run.job_id
            )
        changes = await self._changed(workspace_id, project_id, run)
        rules_changed = await self._rules_changed(workspace_id, project_id, run)
        records: dict[str, set[str]] = {}
        for item in run.inputs:
            if only is not None and item.plan_id != only:
                continue
            if item.plan_id not in cache:
                cache[item.plan_id] = await self._now(workspace_id, project_id, item.plan_id)
            now = cache[item.plan_id]
            then = await self._then(workspace_id, project_id, item)
            part = ScenePart(
                input=item,
                title=now.scene.title if now.scene is not None else "A scene no longer in the plan",
                in_plan=now.scene is not None,
            )
            current: str | None = now.content
            if item.draft_id:
                current = then  # a draft is judged against itself while it waits
                part.staleness = Staleness(gone=then is None)
            else:
                part.staleness = Staleness(
                    gone=now.scene is None or now.content is None or then is None,
                    words_changed=(
                        now.version_id is not None and now.version_id != item.version_id
                    ),
                )
            part.staleness.story_changed = await self._story_touches(
                workspace_id,
                project_id,
                changes,
                now.scene,
                item.plan_id,
                records,
                rules_changed=rules_changed,
            )
            words = then or ""
            for check in run.findings:
                if check.plan_id != item.plan_id:
                    continue
                finding = _as_finding(check)
                judged = (
                    judge_check(
                        key=check_key(words, finding),
                        then=words,
                        now=current,
                        start=check.start,
                        end=check.end,
                        found_now=now.found if not item.draft_id else {check_key(words, finding)},
                        dismissed=now.dismissed,
                    )
                    if then is not None
                    else None
                )
                status: FindingStatus = judged.status if judged else "open"
                part.tally.add(status)
                view.findings.append(
                    FindingView(
                        plan_id=item.plan_id,
                        source="check",
                        code=check.code,
                        severity=check.severity,
                        message=check.message,
                        quoted=_quoted(words, check.start, check.end),
                        status=status,
                        fixed_by=judged.fixed_by if judged else None,
                        dismissal=judged.dismissal if judged else None,
                        # A check that needs no model is worked out again now, so an open one
                        # still holds; only one that could not be (its words are gone) may not.
                        stale=status == "open" and judged is None and part.staleness.stale,
                    )
                )
            target = item.draft_id or item.version_id
            for note in notes:
                if note.target_id != target:
                    continue
                judged = judge_note(
                    key=note_key(words, note),
                    then=words,
                    now=current,
                    start=note.start,
                    end=note.end,
                    dismissed=now.dismissed,
                )
                part.tally.add(judged.status)
                view.findings.append(
                    FindingView(
                        plan_id=item.plan_id,
                        source="note",
                        code=note.category,
                        severity="note",
                        message=note.message,
                        quoted=_quoted(words, note.start, note.end),
                        status=judged.status,
                        fixed_by=judged.fixed_by,
                        dismissal=judged.dismissal,
                        check_pass=note.check_pass,
                        stale=judged.status == "open" and part.staleness.stale,
                    )
                )
            view.scenes.append(part)
        return view


RUN_KINDS: tuple[RunKind, ...] = ("checks", "assistant", "audit")

__all__ = ["RUN_KINDS", "ConsistencyRunService", "FindingView", "RunView"]
