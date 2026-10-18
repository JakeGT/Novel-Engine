"""A deep audit of a part of the story (CON-011).

The author picks a part of the plan (a story, an arc, a beat, or the whole plan) and the
passes to run; the audit runs them over every scene in it that has written text, in the
order a reader meets them. It is one background job: estimated before it starts (the
author is asked first above their threshold), cancellable between calls, and every model
call is checked against the person's limits and recorded once.

Its notes are stored like any check's: against the exact version of each scene it read,
under the audit's job, so they also appear beside that version in the studio. The
checks that need no model (the studio's own checks and the plan's order and reveal
checks) are worked out when the results are read, for free. A stopped or failed audit
keeps what it finished.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from backend.core.logging import log_event
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.check_passes import CheckPass, PassEstimate
from backend.domain.drafting import DraftFinding
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.jobs import Job
from backend.domain.knowledge import reading_order
from backend.domain.sources import SourceVersion
from backend.domain.studio import AUDIT_JOB, AssistantFinding
from backend.domain.tenancy import Permission
from backend.services.job_runner import JobContext, JobSubmission

logger = logging.getLogger(__name__)

# The most scenes one audit reads: a larger part of the story is audited in parts.
MAX_AUDIT_SCENES = 60
WHOLE_PLAN = ""
# A scene's estimate is kept this long, so starting an audit just estimated (and estimating
# again as the author changes the passes) does not gather every scene's context again. It
# is also keyed by the story's version and the scene's text, so a change makes a new one.
ESTIMATE_SECONDS = 120.0
MAX_KEPT_ESTIMATES = 2_000


@dataclass(frozen=True)
class AuditScene:
    """One scene in the part of the story being audited."""

    scene: PlanItem
    source_id: str | None = None
    version: SourceVersion | None = None
    estimates: tuple[PassEstimate, ...] = ()

    @property
    def written(self) -> bool:
        return self.version is not None

    @property
    def tokens(self) -> int:
        return sum(item.total for item in self.estimates)


@dataclass(frozen=True)
class AuditEstimate:
    item: PlanItem | None
    passes: tuple[CheckPass, ...]
    scenes: tuple[AuditScene, ...]

    @property
    def written(self) -> list[AuditScene]:
        return [scene for scene in self.scenes if scene.written]

    def pass_totals(self) -> dict[CheckPass, int]:
        totals = dict.fromkeys(self.passes, 0)
        for scene in self.scenes:
            for item in scene.estimates:
                totals[item.check] = totals.get(item.check, 0) + item.total
        return totals

    @property
    def total(self) -> int:
        return sum(scene.tokens for scene in self.scenes)


@dataclass
class AuditSceneReport:
    plan_id: str
    title: str
    in_plan: bool
    source_id: str | None
    version_id: str
    version_number: int | None
    # Whether the version read is still the scene's newest in this line of the story.
    current: bool
    # checked, partial (stopped or failed part of the way through it: ``passes_done`` says
    # which passes finished), gone (removed from the plan meanwhile), not_checked (stopped
    # or failed first), or waiting (still running).
    status: str
    passes_done: tuple[CheckPass, ...] = ()
    checks: list[DraftFinding] = field(default_factory=list)
    story_checks: list[str] = field(default_factory=list)
    notes: list[AssistantFinding] = field(default_factory=list)
    content: str = ""


@dataclass
class AuditReport:
    job: Job
    item: PlanItem | None
    passes: tuple[CheckPass, ...]
    scenes: list[AuditSceneReport]
    # Scenes in that part of the plan with nothing written when the audit started.
    unwritten: list[PlanItem] = field(default_factory=list)


def _passes(values: Any) -> tuple[CheckPass, ...]:
    from backend.services.studio_service import _passes as choose

    return choose(values)


class StoryAudit:
    """Deep audits, built on the studio's checks (``StudioService``)."""

    def __init__(self, studio: Any) -> None:
        self._studio = studio
        # (story, version of it, scene, text version, passes) -> (kept until, estimates).
        self._estimates: dict[tuple[Any, ...], tuple[float, tuple[PassEstimate, ...]]] = {}

    def register_jobs(self, runner: Any) -> None:
        runner.register(AUDIT_JOB, self.run, keeps_partial=True)

    # ------------------------------------------------------------------ scope

    async def _part(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> tuple[PlanItem | None, list[PlanItem]]:
        """The part of the plan asked about and its scenes, in reading order."""
        studio = self._studio
        item: PlanItem | None = None
        if plan_id != WHOLE_PLAN:
            item = await studio._plan.get(scope, workspace_id, project_id, plan_id)
            if not item.is_active:
                raise ValidationError(
                    "That part of the plan was taken out of it, so there is nothing to audit."
                )
            if item.kind is PlanKind.THREAD:
                raise ValidationError(
                    "A thread runs across the plan: audit the parts of the story it runs through."
                )
        items = await studio._reader.list_plan_items(
            workspace_id=workspace_id, project_id=project_id
        )
        order = reading_order(items)
        if item is None:
            return None, order
        if item.kind is PlanKind.SCENE:
            return item, [item]
        parents = {entry.id: entry.parent_id for entry in items}

        def under(scene: PlanItem) -> bool:
            seen: set[str] = set()
            parent = scene.parent_id
            while parent and parent not in seen:
                if parent == item.id:
                    return True
                seen.add(parent)
                parent = parents.get(parent)
            return False

        return item, [scene for scene in order if under(scene)]

    async def estimate(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        passes: Any = None,
    ) -> AuditEstimate:
        """What auditing this part of the story would read, scene by scene and pass by pass.
        Calls no model."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        chosen = _passes(passes)
        item, scenes = await self._part(scope, workspace_id, project_id, plan_id)
        branch = await studio._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        story = (workspace_id, project_id, branch.id, branch.head_revision)
        found: list[AuditScene] = []
        for scene in scenes:
            text = await studio._text(workspace_id, project_id, scene.id)
            if text is None:
                found.append(AuditScene(scene))
                continue
            _, current, _ = await studio._sources.version(workspace_id, project_id, text.id)
            estimates: tuple[PassEstimate, ...] = ()
            if chosen and len([entry for entry in found if entry.written]) < MAX_AUDIT_SCENES:
                estimates = await self._scene_estimates(scope, story, scene.id, current, chosen)
            found.append(AuditScene(scene, text.id, current, estimates))
        return AuditEstimate(item=item, passes=chosen, scenes=tuple(found))

    async def _scene_estimates(
        self,
        scope: AccessScope,
        story: tuple[str, str, str, int],
        scene_id: str,
        version: SourceVersion,
        chosen: tuple[CheckPass, ...],
    ) -> tuple[PassEstimate, ...]:
        """One scene's estimate, pass by pass: worked out once, then kept a short while."""
        key = (*story, scene_id, version.id, chosen)
        now = time.monotonic()
        kept = self._estimates.get(key)
        if kept is not None and kept[0] > now:
            return kept[1]
        workspace_id, project_id = story[0], story[1]
        plan = await self._studio.check_plan(
            scope, workspace_id, project_id, scene_id, version.content, chosen
        )
        if len(self._estimates) >= MAX_KEPT_ESTIMATES:
            self._estimates = {k: v for k, v in self._estimates.items() if v[0] > now}
            if len(self._estimates) >= MAX_KEPT_ESTIMATES:
                self._estimates.clear()
        self._estimates[key] = (now + ESTIMATE_SECONDS, plan.estimates)
        return plan.estimates  # type: ignore[no-any-return]

    # ------------------------------------------------------------------ start

    async def start(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        passes: Any = None,
        confirmed_tokens: int | None = None,
        idempotency_key: str = "",
    ) -> Job:
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        existing = await studio._jobs.find_by_key(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=scope.actor_user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing  # type: ignore[no-any-return]
        found = await self.estimate(scope, workspace_id, project_id, plan_id, passes)
        written = found.written
        if not written:
            raise ValidationError(
                "Nothing in this part of the story is written yet, so there is nothing to audit."
            )
        if len(written) > MAX_AUDIT_SCENES:
            raise ValidationError(
                f"This part of the story has {len(written)} written scenes; an audit reads at "
                f"most {MAX_AUDIT_SCENES}. Audit a smaller part of it."
            )
        total = found.total
        if found.passes:
            await studio.confirm(
                workspace_id,
                project_id,
                estimated_tokens=total,
                confirmed_tokens=confirmed_tokens,
                doing="Auditing this part of the story",
            )
            if studio._usage is not None:
                await studio._usage.ensure_within_limits(
                    workspace_id=workspace_id,
                    actor_user_id=scope.actor_user_id,
                    estimated_tokens=total,
                )
        return await studio._runner.submit(  # type: ignore[no-any-return]
            scope,
            workspace_id,
            project_id,
            JobSubmission(
                kind=AUDIT_JOB,
                payload={
                    "plan_id": plan_id,
                    "branch_id": await studio._draft_branch(workspace_id, project_id),
                    "passes": [item.value for item in found.passes],
                    # The exact versions read: an edit after starting is not audited.
                    "scenes": [
                        {
                            "plan_id": entry.scene.id,
                            "source_id": entry.source_id,
                            "version_id": entry.version.id if entry.version else None,
                        }
                        for entry in written
                    ],
                    "unwritten": [entry.scene.id for entry in found.scenes if not entry.written],
                    "estimated_tokens": total,
                },
                idempotency_key=idempotency_key,
            ),
        )

    # -------------------------------------------------------------------- run

    async def run(self, ctx: JobContext) -> dict:
        studio = self._studio
        job = ctx.job
        scope = ctx.scope
        workspace_id, project_id = job.workspace_id, job.project_id
        payload = job.payload
        chosen = _passes(payload.get("passes") or [])
        scenes = list(payload.get("scenes") or [])
        # Each scene as it is finished, and the one under way once a pass of it is done
        # ("partial", with the passes finished): kept if the audit stops part of the way.
        done: list[dict[str, Any]] = []
        ctx.partial = {"scenes": done}
        # An audit taken over from a worker that stopped, or put back when the app was
        # stopping, starts again from the top: a pass whose notes an earlier run of it
        # already stored is not asked for again, so its notes are not stored twice.
        earlier: dict[tuple[str, str], int] = {}
        if chosen:
            for note in await studio._store.findings_for_job(
                workspace_id=workspace_id, project_id=project_id, job_id=job.id
            ):
                where = (note.target_id, note.check_pass or note.category)
                earlier[where] = earlier.get(where, 0) + 1
        provider = None
        total = len(scenes)
        runner = studio.pass_runner()
        for index, entry in enumerate(scenes):
            prefix = f"Scene {index + 1} of {total}: "
            lo = 5 + int(90 * index / max(total, 1))
            hi = 5 + int(90 * (index + 1) / max(total, 1))
            await ctx.step(f"{prefix}reading", lo)
            try:
                _, version, _ = await studio._sources.version(
                    workspace_id, project_id, entry["source_id"], entry["version_id"]
                )
            except NotFoundError:
                done.append({**entry, "status": "gone", "findings": 0, "dropped": 0})
                continue
            scene = await studio._reader.get_plan_item(
                workspace_id=workspace_id, project_id=project_id, plan_id=entry["plan_id"]
            )
            if scene is None or not scene.is_active:
                # Removed from the plan since the audit started: nothing to check it against.
                done.append({**entry, "status": "gone", "findings": 0, "dropped": 0})
                continue
            record: dict[str, Any] = {
                **entry,
                "status": "partial",
                "findings": 0,
                "dropped": 0,
                "passes": {},
            }
            if chosen:
                try:
                    plan = await studio.check_plan(
                        scope, workspace_id, project_id, entry["plan_id"], version.content, chosen
                    )
                except NotFoundError:
                    # Removed from the plan since the audit started: nothing to check it against.
                    done.append({**entry, "status": "gone", "findings": 0, "dropped": 0})
                    continue

                def call_key(check: CheckPass, number: int = index) -> str:
                    return f"{job.id}:{job.attempts}:{number}:{check.value}"

                count = len(plan.materials)
                for number, (material, estimate) in enumerate(
                    zip(plan.materials, plan.estimates, strict=True)
                ):
                    check = material.check
                    kept = earlier.get((version.id, check.value), 0)
                    if kept:
                        # Finished by an earlier run of this audit: its notes are kept.
                        outcome = {"findings": kept, "dropped": 0}
                    else:
                        first = provider is None
                        if provider is None:
                            # Resolved once, with what the whole audit was estimated to use.
                            provider = await studio._provider(
                                scope,
                                estimated_tokens=int(payload.get("estimated_tokens") or 0),
                                idempotency_key=call_key(check),
                            )
                        result = await runner.run(
                            ctx,
                            materials=(material,),
                            content=version.content,
                            pov=plan.point_of_view,
                            target_kind="version",
                            target_id=version.id,
                            provider=provider,
                            call_key=call_key,
                            # Each call is checked for what it needs: the audit goes as far
                            # as the allowance lets it, and keeps what it finished.
                            remaining_tokens=lambda _number, found=estimate: found.total,
                            progress=(
                                lo + int((hi - lo) * number / max(count, 1)),
                                lo + int((hi - lo) * (number + 1) / max(count, 1)),
                            ),
                            stage=prefix,
                            first_call_checked=first,
                        )
                        outcome = result.result()["passes"][check.value]
                    record["passes"][check.value] = outcome
                    record["findings"] += int(outcome["findings"])
                    record["dropped"] += int(outcome["dropped"])
                    if not any(item is record for item in done):
                        done.append(record)
            record["status"] = "checked"
            if not any(item is record for item in done):
                done.append(record)
        await ctx.step("Finishing", 98)
        log_event(
            logger,
            "audit.finished",
            scenes=len(done),
            passes=len(chosen),
            kept=sum(int(item.get("findings", 0)) for item in done),
        )
        return {"scenes": done}

    # ----------------------------------------------------------------- results

    async def recent(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> list[Job]:
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await studio._jobs.recent(  # type: ignore[no-any-return]
            workspace_id=workspace_id,
            project_id=project_id,
            kind=AUDIT_JOB,
            plan_id=plan_id,
            limit=5,
            branch_id=await studio._draft_branch(workspace_id, project_id),
        )

    async def report(
        self, scope: AccessScope, workspace_id: str, project_id: str, job_id: str
    ) -> AuditReport:
        """An audit's results, scene by scene: the notes it found in the version it read,
        and the checks that need no model, worked out now for that version."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        job = await studio._jobs.get(
            workspace_id=workspace_id, project_id=project_id, job_id=job_id
        )
        if job is None or job.kind != AUDIT_JOB:
            raise NotFoundError("That audit was not found.")
        payload = job.payload
        plan_id = str(payload.get("plan_id") or WHOLE_PLAN)
        item: PlanItem | None = None
        items = await studio._reader.list_plan_items(
            workspace_id=workspace_id, project_id=project_id
        )
        by_id = {entry.id: entry for entry in items if entry.is_active}
        if plan_id != WHOLE_PLAN:
            item = by_id.get(plan_id)
        notes = await studio._store.findings_for_job(
            workspace_id=workspace_id, project_id=project_id, job_id=job.id
        )
        recorded = {
            str(entry.get("plan_id")): entry for entry in (job.result or {}).get("scenes", [])
        }
        finished = {plan: str(entry.get("status") or "checked") for plan, entry in recorded.items()}
        story_checks: dict[str, list[str]] = {}
        try:
            reading = await studio._plan.outline(scope, workspace_id, project_id)
            for found in reading.open:
                if found.kind == "problem":
                    story_checks.setdefault(found.plan_id, []).append(found.message)
        except Exception as error:  # noqa: BLE001 - the plan's own checks are extra here
            log_event(logger, "audit.story_checks_skipped", error_type=type(error).__name__)
        report_passes = _passes(payload.get("passes") or [])
        scenes: list[AuditSceneReport] = []
        for entry in payload.get("scenes") or []:
            scene_id = str(entry.get("plan_id"))
            scene = by_id.get(scene_id)
            version_id = str(entry.get("version_id"))
            report = AuditSceneReport(
                plan_id=scene_id,
                title=scene.title if scene else "A scene no longer in the plan",
                in_plan=scene is not None,
                source_id=entry.get("source_id"),
                version_id=version_id,
                version_number=None,
                current=False,
                status=finished.get(scene_id)
                or ("waiting" if not job.status.finished else "not_checked"),
                story_checks=story_checks.get(scene_id, []),
                notes=[note for note in notes if note.target_id == version_id],
            )
            if report.status == "partial":
                ran = (recorded[scene_id].get("passes") or {}).keys()
                report.passes_done = tuple(check for check in report_passes if check.value in ran)
            try:
                _, version, _ = await studio._sources.version(
                    workspace_id, project_id, entry.get("source_id") or "", version_id
                )
            except NotFoundError:
                scenes.append(report)
                continue
            report.version_number = version.number
            report.content = version.content
            if scene is not None:
                text = await studio._text(workspace_id, project_id, scene.id)
                if text is not None:
                    _, current, _ = await studio._sources.version(workspace_id, project_id, text.id)
                    report.current = current.id == version.id
                brief = await studio.brief(workspace_id, project_id, scene)
                report.checks = await studio._check(
                    workspace_id,
                    project_id,
                    report.source_id,
                    version.content,
                    brief,
                    scene_id=scene.id,
                )
            scenes.append(report)
        unwritten = [by_id[pid] for pid in payload.get("unwritten") or [] if pid in by_id]
        return AuditReport(
            job=job,
            item=item,
            passes=report_passes,
            scenes=scenes,
            unwritten=unwritten,
        )


__all__ = [
    "MAX_AUDIT_SCENES",
    "WHOLE_PLAN",
    "AuditEstimate",
    "AuditReport",
    "AuditScene",
    "AuditSceneReport",
    "StoryAudit",
]
