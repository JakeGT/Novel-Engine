"""Checks kept as runs, and what they come to now (CON-001, CON-007, CON-016).

A run is recorded when the studio's checks that need no model run on a saved or kept
version (once per set of inputs), when a check with the assistant reads the scene, and
when an audit starts reading. Each run keeps the story's checking profile and a
fingerprint of every input its checks read (``backend.domain.check_fingerprints``); the
checks themselves run incrementally (``SceneCheckRunner``), so a check whose inputs did
not change is not worked out again.

A run is read back judged against the story as it is now: each finding open, accepted,
dismissed, superseded (found again by a later check) or fixed, and each scene marked out
of date when its words, what it names in the story bible, or the story rules changed
since. Nothing about that is stored, so it cannot drift; nothing here ever blocks a
change or calls a model.

Pictures (CON-001). A check with the assistant that compares the scene with its pictures
also keeps which pictures it was given (each picture and version, how it was to be
given, and a digest of its accepted description; never the picture or the words).
Read back, each picture says whether it has changed since (a newer version, an accepted
description changed, no longer given with the scene, now left out), and each note of
that pass names the picture it was compared with and is out of date when that picture
(or, when it names none, any of them) changed.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.check_fingerprints import (
    CHECKS_VERSION,
    check_for,
    code_words,
    finding_dependency,
    prose_fingerprint,
    rules_fingerprint,
)
from backend.domain.check_passes import CheckPass, pass_material
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
from backend.domain.dismissals import DismissReason, FindingDismissal, FindingKind
from backend.domain.drafting import DraftFinding, Severity
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.jobs import Job
from backend.domain.knowledge import SCENE_TIME_KEY
from backend.domain.studio import AssistantFinding
from backend.domain.tenancy import Permission
from backend.services.incremental_checks import CheckWork, SceneCheckRunner

logger = logging.getLogger(__name__)

MAX_QUOTED = 400
# How many runs the calibration note reads, newest first, in one line of the story.
CALIBRATION_RUNS = 200
# A rate is said only once a check has found something at least this many times
# (a check that spoke twice and was dismissed once says nothing yet).
MIN_CALIBRATION_FINDINGS = 5
# At or above this share dismissed as "not a problem", the note says the check is often
# wrong for this story. Nothing changes on its own: thresholds are the author's choice.
OFTEN_DISMISSED = 0.5


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
    # Dismissed before, and back because what it says has changed since.
    came_back: bool = False
    # For a check that needs no model, the named check it belongs to.
    check: str | None = None
    # For a note of the pictures pass, the picture it was compared with (CON-001).
    picture: PictureRead | None = None


@dataclass(frozen=True)
class PictureRead:
    """A picture a check with the assistant compared the scene with, as it was then."""

    asset_id: str
    version_id: str
    # The picture's name as the check was given it.
    label: str
    # How it was to be given: send (the picture itself) or describe (its description).
    choice: str
    # A digest of its accepted description then; "" when it had none.
    described: str = ""
    # Plain words when it differs now; None when it is as it was (or cannot be told).
    changed: str | None = None

    def as_json(self) -> dict[str, str]:
        return {
            "asset_id": self.asset_id,
            "version_id": self.version_id,
            "label": self.label,
            "choice": self.choice,
            "described": self.described,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> PictureRead:
        return PictureRead(
            asset_id=str(raw.get("asset_id") or ""),
            version_id=str(raw.get("version_id") or ""),
            label=str(raw.get("label") or ""),
            choice=str(raw.get("choice") or ""),
            described=str(raw.get("described") or ""),
        )


def _digest(words: str) -> str:
    return hashlib.sha256(words.strip().encode("utf-8")).hexdigest()[:16] if words.strip() else ""


def picture_changes(then: list[PictureRead], now: list[PictureRead]) -> list[PictureRead]:
    """The pictures a run read, each with what has changed about it since, in plain words."""
    current = {item.asset_id: item for item in now}
    out: list[PictureRead] = []
    for item in then:
        found = current.get(item.asset_id)
        changed: str | None = None
        if found is None:
            changed = "It is no longer given with the scene, or is now left out."
        elif found.version_id != item.version_id:
            changed = "A newer version of it has been added since."
        elif found.described != item.described:
            changed = "Its accepted description has changed since."
        elif found.choice != item.choice:
            changed = "It is now given another way (the picture itself or its description)."
        out.append(
            PictureRead(
                asset_id=item.asset_id,
                version_id=item.version_id,
                label=item.label,
                choice=item.choice,
                described=item.described,
                changed=changed,
            )
        )
    return out


def compared_with(message: str, pictures: list[PictureRead]) -> PictureRead | None:
    """The picture a note of the pictures pass names ("Compared with the picture …")."""
    marker = "Compared with the picture "
    if marker not in message:
        return None
    named = message.rsplit(marker, 1)[1].rstrip(".").strip()
    for item in pictures:
        label = item.label[:120].strip()
        if label and (named == label or named.startswith(label) or label.startswith(named)):
            return item
    return None


@dataclass(frozen=True)
class PassTiming:
    """One pass of a check with the assistant, as its job recorded it (CON-001)."""

    check_pass: str
    plan_id: str | None
    ms: float | None
    calls: int
    # Finished by an earlier run of the same job (taken over or tried again).
    earlier: bool = False


@dataclass
class RunView:
    run: ConsistencyRun
    title: str
    # The run's job status, or "done" for the checks that need no model.
    status: str
    scenes: list[ScenePart] = field(default_factory=list)
    findings: list[FindingView] = field(default_factory=list)
    # How a check with the assistant was sent, pass by pass (CON-013), from its job.
    coverage: dict[str, Any] = field(default_factory=dict)
    # The pictures the pictures pass was given, by scene, each saying what changed since.
    pictures: dict[str, list[PictureRead]] = field(default_factory=dict)
    # How long each assistant pass took, the calls it made, from its job (CON-001).
    timings: list[PassTiming] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return any(part.staleness.stale for part in self.scenes) or any(
            item.changed for items in self.pictures.values() for item in items
        )


@dataclass(frozen=True)
class CalibrationLine:
    """How often the author dismissed one check's findings in this line of the story."""

    code: str
    words: str
    source: Literal["check", "note"]
    # Distinct findings it gave, in the runs read.
    found: int
    # Dismissed as not a problem (or with the author's own reason), and kept on purpose.
    dismissed: int
    accepted: int
    # The share dismissed as not a problem, once there is enough to say it.
    rate: float | None
    note: str


@dataclass
class _Now:
    """A scene as it is now: its words and what the checks find in them."""

    scene: PlanItem | None
    source_id: str | None = None
    version_id: str | None = None
    content: str | None = None
    # Each finding found now (by fingerprint) and what it depends on.
    found: dict[str, str | None] = field(default_factory=dict)
    dismissed: dict[str, FindingDismissal] = field(default_factory=dict)
    # The wider dismissals standing now (a character, a stretch of the story; CON-016).
    scoping: Any = None

    def dismissed_for(self, key: str, kind: Any, code: str, words: str) -> dict[str, Any]:
        """The scene's dismissals, with a wider one that covers this finding as its own."""
        if key in self.dismissed or self.scoping is None or self.scene is None:
            return self.dismissed
        covering = self.scoping.covering(self.scene.id, kind, code, words)
        if covering is None:
            return self.dismissed
        return {**self.dismissed, key: covering}


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


def dependency_of(finding: DraftFinding) -> str:
    """What a check's finding depends on (CON-016)."""
    return finding_dependency(finding.code, finding.subject, finding.message)


class ConsistencyRunService:
    """Records runs and reads them back judged; built on the studio (``StudioService``)."""

    def __init__(self, *, store: Any, studio: Any) -> None:
        self._store = store
        self._studio = studio
        # The studio's checks that need no model, worked out only where inputs changed.
        self.checks = SceneCheckRunner(studio=studio, cache=store)

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
    ) -> CheckWork:
        return await self.checks.run(workspace_id, project_id, scene, source_id or None, content)

    async def _rules(self, workspace_id: str, project_id: str) -> list[Any] | None:
        rules = getattr(getattr(self._studio, "_context", None), "_rules", None)
        if rules is None:
            return None
        return list(await rules.list_rules(workspace_id=workspace_id, project_id=project_id))

    async def _rules_fingerprint(self, workspace_id: str, project_id: str) -> str | None:
        listed = await self._rules(workspace_id, project_id)
        return rules_fingerprint(listed) if listed is not None else None

    async def _profile(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        """How much the story checks on its own now, and the versions of the checks."""
        from backend.domain.review_bundles import POLICY_VERSION

        profile: dict[str, Any] = {"checks": CHECKS_VERSION, "bundles": POLICY_VERSION}
        controls = getattr(self._studio, "controls", None)
        if controls is not None:
            try:
                settings = await controls.for_story(workspace_id, project_id)
                profile["checking"] = settings.check_profile.value
            except Exception as error:  # noqa: BLE001 - the profile is extra to the record
                log_event(logger, "checks.profile_unread", error_type=type(error).__name__)
        return profile

    async def _fingerprints(
        self, workspace_id: str, project_id: str, works: dict[str, CheckWork]
    ) -> dict[str, Any]:
        found: dict[str, Any] = {
            "scenes": {plan_id: work.as_json() for plan_id, work in works.items()}
        }
        rules = await self._rules_fingerprint(workspace_id, project_id)
        if rules is not None:
            found["rules"] = rules
        return found

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
        per set of inputs: when nothing any check reads has changed since the last record,
        that record is the answer. ``asked`` when the author asked for it, so nothing
        written is said plainly rather than skipped."""
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
        work = await self._checks_of(workspace_id, project_id, scene, text.id, current.content)
        latest = await self._store.recent(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            plan_id=plan_id,
            kind="checks",
            limit=1,
        )
        rules = await self._rules_fingerprint(workspace_id, project_id)
        # The same run only for the same words, checks, version of the story and rules: a
        # run judged out of date (the rules changed since, say) is never the answer.
        if (
            latest
            and latest[0].inputs
            and latest[0].inputs[0].version_id == current.id
            and latest[0].scene_fingerprints(plan_id).get("checks") == work.checks
            and latest[0].story_revision == head
            and latest[0].rules == rules
        ):
            return latest[0]  # type: ignore[no-any-return]
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
            findings=_recorded(plan_id, work.findings),
            created_by=scope.actor_user_id,
            profile=await self._profile(workspace_id, project_id),
            fingerprints=await self._fingerprints(workspace_id, project_id, {plan_id: work}),
        )
        await self._store.add(run)
        log_event(
            logger,
            "checks.run_recorded",
            kind="checks",
            findings=len(work.findings),
            ran=len(work.ran),
            reused=len(work.reused),
        )
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
        work = await self._checks_of(workspace_id, project_id, scene, source_id or "", content)
        fingerprints = await self._fingerprints(workspace_id, project_id, {plan_id: work})
        if CheckPass.PICTURES.value in passes:
            pictures = await self._pictures_read(
                job.envelope().scope(), workspace_id, project_id, plan_id
            )
            if pictures is not None:
                fingerprints["pictures"] = {plan_id: [item.as_json() for item in pictures]}
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
                findings=_recorded(plan_id, work.findings),
                job_id=job.id,
                created_by=job.actor_user_id,
                profile=await self._profile(workspace_id, project_id),
                fingerprints=fingerprints,
            )
        )
        if added:
            log_event(logger, "checks.run_recorded", kind="assistant", passes=len(passes))

    async def _pictures_read(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> list[PictureRead] | None:
        """The pictures the pictures pass reads for this scene now, as the scene's context
        gives them (left-out pictures are not read). None when that cannot be told."""
        context = getattr(self._studio, "_context", None)
        if context is None or not hasattr(context, "for_scene"):
            return None
        try:
            built = await context.for_scene(scope, workspace_id, project_id, plan_id)
        except Exception as error:  # noqa: BLE001 - the record is extra to the check
            log_event(logger, "checks.pictures_unread", error_type=type(error).__name__)
            return None
        material = pass_material(CheckPass.PICTURES, built.manifest)
        return [
            PictureRead(
                asset_id=part.asset_id,
                version_id=part.version_id,
                label=part.label[:120],
                choice=part.choice.value,
                described=_digest(part.fallback_text),
            )
            for part in material.pictures
        ]

    async def record_audit(self, job: Job) -> None:
        """An audit, as it starts reading: once per job, the exact versions it reads and
        what the checks that need no model find in each."""
        workspace_id, project_id = job.workspace_id, job.project_id
        branch_id, head = await self._line(workspace_id, project_id)
        if await self._store.for_job(
            workspace_id=workspace_id, project_id=project_id, job_id=job.id, branch_id=branch_id
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
        works: dict[str, CheckWork] = {}
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
                work = await self._checks_of(
                    workspace_id, project_id, scene, item.source_id or "", content
                )
                works[item.plan_id] = work
                found += _recorded(item.plan_id, work.findings)
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
                profile=await self._profile(workspace_id, project_id),
                fingerprints=await self._fingerprints(workspace_id, project_id, works),
            )
        )
        log_event(logger, "checks.run_recorded", kind="audit", scenes=len(inputs))

    # ------------------------------------------------------------------ reading

    async def history(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> list[RunView]:
        """The runs that read this scene, newest first, each judged for this scene; an open
        finding a newer run found again is superseded."""
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
        later: dict[str, set[str]] = {}
        pictures_now: dict[str, list[PictureRead] | None] = {}
        views: list[RunView] = []
        for run in runs:
            view = await self._judge(
                workspace_id,
                project_id,
                run,
                only=plan_id,
                cache=cache,
                later=later,
                scope=scope,
                pictures_now=pictures_now,
            )
            for key in await self._keys_of(workspace_id, project_id, run, plan_id):
                later.setdefault(plan_id, set()).add(key)
            views.append(view)
        return views

    async def _later_keys(
        self, workspace_id: str, project_id: str, run: ConsistencyRun
    ) -> dict[str, set[str]]:
        """For each scene a run read, what newer runs of that scene found."""
        found: dict[str, set[str]] = {}
        for item in run.inputs:
            newer = await self._store.recent(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=run.branch_id,
                plan_id=item.plan_id,
                limit=MAX_HISTORY,
            )
            keys: set[str] = set()
            for other in newer:
                if other.id == run.id or (
                    run.created_at is not None
                    and other.created_at is not None
                    and other.created_at <= run.created_at
                ):
                    continue
                keys |= await self._keys_of(workspace_id, project_id, other, item.plan_id)
            found[item.plan_id] = keys
        return found

    async def _keys_of(
        self, workspace_id: str, project_id: str, run: ConsistencyRun, plan_id: str
    ) -> set[str]:
        """The fingerprint of every finding a run gave in one scene."""
        from backend.services.studio_service import check_key, note_key

        keys: set[str] = set()
        for item in run.inputs:
            if item.plan_id != plan_id:
                continue
            words = await self._then(workspace_id, project_id, item)
            if words is None:
                continue
            keys |= {
                check_key(words, _as_finding(check))
                for check in run.findings
                if check.plan_id == plan_id
            }
            if run.job_id is not None:
                target = item.draft_id or item.version_id
                notes = await self._studio._store.findings_for_job(
                    workspace_id=workspace_id, project_id=project_id, job_id=run.job_id
                )
                keys |= {note_key(words, note) for note in notes if note.target_id == target}
        return keys

    async def get(
        self, scope: AccessScope, workspace_id: str, project_id: str, run_id: str
    ) -> RunView:
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        branch_id, _ = await self._line(workspace_id, project_id)
        run = await self._store.get(
            workspace_id=workspace_id, project_id=project_id, run_id=run_id, branch_id=branch_id
        )
        if run is None:
            raise NotFoundError("That check was not found.")
        later = await self._later_keys(workspace_id, project_id, run)
        return await self._judge(
            workspace_id, project_id, run, only=None, cache={}, later=later, scope=scope
        )

    async def for_job(
        self, scope: AccessScope, workspace_id: str, project_id: str, job_id: str
    ) -> RunView:
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        branch_id, _ = await self._line(workspace_id, project_id)
        run = await self._store.for_job(
            workspace_id=workspace_id, project_id=project_id, job_id=job_id, branch_id=branch_id
        )
        if run is None:
            raise NotFoundError("That check has no record yet.")
        later = await self._later_keys(workspace_id, project_id, run)
        return await self._judge(
            workspace_id, project_id, run, only=None, cache={}, later=later, scope=scope
        )

    async def _now(
        self, workspace_id: str, project_id: str, plan_id: str, *, scoping: Any = None
    ) -> _Now:
        """A scene as it is now. ``scoping`` is the wider dismissals already worked out in
        this call, used again rather than read for every scene."""
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
            work = await self._checks_of(workspace_id, project_id, scene, text.id, current.content)
            from backend.services.studio_service import check_key

            now.found = {
                check_key(current.content, item): dependency_of(item) for item in work.findings
            }
        branch_id, _ = await self._line(workspace_id, project_id)
        moment = datetime.now(UTC)
        standing = await studio._store.dismissals(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, plan_id=plan_id
        )
        now.dismissed = {item.fingerprint: item for item in standing if item.applies(moment)}
        scoped = getattr(studio, "scoped", None)
        if scoping is not None:
            now.scoping = scoping
        elif scoped is not None:
            now.scoping = await scoped.scoping(workspace_id, project_id)
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
        is about the whole story, so every scene's findings may hold differently. A run
        that kept the rules' fingerprint is compared with it; an older one by time."""
        listed = await self._rules(workspace_id, project_id)
        if listed is None:
            return False
        if run.rules is not None:
            return rules_fingerprint(listed) != run.rules
        if run.created_at is None:
            return False
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
                    # Amending or removing an event names only the event (an amendment
                    # that moves who took part, from a merge, names them itself).
                    change.record_type == "event" and not change.change.get("participants")
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

    async def _job(self, workspace_id: str, project_id: str, run: ConsistencyRun) -> Job | None:
        if run.job_id is None:
            return None
        return await self._studio._jobs.get(  # type: ignore[no-any-return]
            workspace_id=workspace_id, project_id=project_id, job_id=run.job_id
        )

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
        later: dict[str, set[str]] | None = None,
        scope: AccessScope | None = None,
        pictures_now: dict[str, list[PictureRead] | None] | None = None,
    ) -> RunView:
        from backend.services.scoped_dismissals import finding_words
        from backend.services.studio_service import check_key, note_key

        job = await self._job(workspace_id, project_id, run)
        view = RunView(
            run=run,
            title=await self._title(workspace_id, project_id, run),
            status="done" if job is None else str(job.status.value),
            coverage=_coverage(job),
            timings=_timings(job),
        )
        notes: list[AssistantFinding] = []
        if run.job_id is not None:
            notes = await self._studio._store.findings_for_job(
                workspace_id=workspace_id, project_id=project_id, job_id=run.job_id
            )
        changes = await self._changed(workspace_id, project_id, run)
        rules_changed = await self._rules_changed(workspace_id, project_id, run)
        records: dict[str, set[str]] = {}
        texts: dict[str, str | None] = {}
        for item in run.inputs:
            if only is not None and item.plan_id != only:
                continue
            if item.plan_id not in cache:
                # The wider dismissals (the whole plan and every character's names) are
                # the same for every scene: worked out once per call.
                shared = next((n.scoping for n in cache.values() if n.scoping is not None), None)
                cache[item.plan_id] = await self._now(
                    workspace_id, project_id, item.plan_id, scoping=shared
                )
            now = cache[item.plan_id]
            then = await self._then(workspace_id, project_id, item)
            again = (later or {}).get(item.plan_id, set())
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
                recorded = run.scene_input(item.plan_id, "prose")
                if recorded is not None and now.content is not None:
                    # By the words themselves: a copy of the same words (a what-if's own
                    # copy of the scene) has not changed.
                    changed = prose_fingerprint(now.content) != recorded
                else:
                    changed = now.version_id is not None and now.version_id != item.version_id
                part.staleness = Staleness(
                    gone=now.scene is None or now.content is None or then is None,
                    words_changed=changed,
                )
            recorded_pictures = (run.fingerprints.get("pictures") or {}).get(item.plan_id)
            read: list[PictureRead] = []
            if isinstance(recorded_pictures, list):
                then_pictures = [PictureRead.from_json(raw) for raw in recorded_pictures]
                found_now: list[PictureRead] | None = None
                if scope is not None:
                    seen = pictures_now if pictures_now is not None else {}
                    if item.plan_id not in seen:
                        seen[item.plan_id] = await self._pictures_read(
                            scope, workspace_id, project_id, item.plan_id
                        )
                    found_now = seen[item.plan_id]
                read = (
                    picture_changes(then_pictures, found_now)
                    if found_now is not None
                    else then_pictures
                )
                view.pictures[item.plan_id] = read
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
                key = check_key(words, finding)
                judged = (
                    judge_check(
                        key=key,
                        then=words,
                        now=current,
                        start=check.start,
                        end=check.end,
                        found_now=now.found if not item.draft_id else {key: dependency_of(finding)},
                        dismissed=now.dismissed_for(
                            key,
                            FindingKind.CHECK,
                            check.code,
                            finding_words(check.subject),
                        ),
                    )
                    if then is not None
                    else None
                )
                status: FindingStatus = judged.status if judged else "open"
                if status == "open" and key in again:
                    status = "superseded"
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
                        came_back=bool(judged and judged.came_back),
                        check=check_for(check.code).name,
                    )
                )
            target = item.draft_id or item.version_id
            for note in notes:
                if note.target_id != target:
                    continue
                key = note_key(words, note)
                judged_note = judge_note(
                    key=key,
                    then=words,
                    now=current,
                    start=note.start,
                    end=note.end,
                    dismissed=now.dismissed_for(
                        key, FindingKind.NOTE, note.check_pass or note.category, note.message
                    ),
                )
                status = judged_note.status
                if status == "open" and key in again:
                    status = "superseded"
                part.tally.add(status)
                picture: PictureRead | None = None
                pictures_moved = False
                if note.check_pass == CheckPass.PICTURES.value and read:
                    picture = compared_with(note.message, read)
                    pictures_moved = (
                        bool(picture.changed)
                        if picture is not None
                        else any(entry.changed for entry in read)
                    )
                view.findings.append(
                    FindingView(
                        plan_id=item.plan_id,
                        source="note",
                        code=note.category,
                        severity="note",
                        message=note.message,
                        quoted=_quoted(words, note.start, note.end),
                        status=status,
                        fixed_by=judged_note.fixed_by,
                        dismissal=judged_note.dismissal,
                        check_pass=note.check_pass,
                        stale=status == "open" and (part.staleness.stale or pictures_moved),
                        picture=picture,
                    )
                )
            view.scenes.append(part)
            texts[item.plan_id] = then
        _passage_words(view.coverage, texts, run.plan_id if run.scope == "scene" else None)
        return view

    # -------------------------------------------------------------- calibration

    async def calibration(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[CalibrationLine]:
        """How often each check's findings were dismissed in this line of the story: a note
        for the author, never a change to what the checks do (plan/13: calibration is an
        explicit choice, never silent learning)."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        branch_id, _ = await self._line(workspace_id, project_id)
        runs = await self._store.recent(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            limit=CALIBRATION_RUNS,
        )
        found: dict[tuple[str, str], set[tuple[Any, ...]]] = {}
        seen_jobs: set[str] = set()
        for run in runs:
            for check in run.findings:
                found.setdefault(("check", check.code), set()).add(
                    (check.plan_id, check.subject, check.message)
                )
            if run.job_id is not None and run.job_id not in seen_jobs:
                seen_jobs.add(run.job_id)
                for note in await studio._store.findings_for_job(
                    workspace_id=workspace_id, project_id=project_id, job_id=run.job_id
                ):
                    found.setdefault(("note", note.category), set()).add(
                        (note.target_id, note.start, note.end)
                    )
        dismissals = await studio._store.dismissals_in(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        decided: dict[tuple[str, str], list[FindingDismissal]] = {}
        for dismissal in dismissals:
            if dismissal.withdrawn_at is not None:
                continue
            decided.setdefault((dismissal.kind.value, dismissal.code), []).append(dismissal)
        from backend.domain.check_passes import PASS_LABELS, CheckPass

        lines: list[CalibrationLine] = []
        for source, code in sorted(set(found) | set(decided)):
            count = len(found.get((source, code), set()))
            mine = decided.get((source, code), [])
            accepted = sum(1 for item in mine if item.reason is DismissReason.INTENDED)
            dismissed = len(mine) - accepted
            # A dismissal of a finding older than the runs read still counts it as found.
            count = max(count, len(mine))
            if source == "note":
                try:
                    words = PASS_LABELS[CheckPass(code)]
                except ValueError:
                    words = code.replace("_", " ").capitalize()
            else:
                words = code_words(code)
            rate = dismissed / count if count >= MIN_CALIBRATION_FINDINGS else None
            lines.append(
                CalibrationLine(
                    code=code,
                    words=words,
                    source="note" if source == "note" else "check",
                    found=count,
                    dismissed=dismissed,
                    accepted=accepted,
                    rate=rate,
                    note=_calibration_note(count, dismissed, accepted, rate),
                )
            )
        lines.sort(key=lambda line: (-(line.rate or 0.0), -line.found, line.words))
        return lines


def _times(number: int) -> str:
    return "once" if number == 1 else "twice" if number == 2 else f"{number} times"


def calibration_note(found: int, dismissed: int, accepted: int, rate: float | None) -> str:
    """How often one kind of finding was dismissed, in words for the author."""
    return _calibration_note(found, dismissed, accepted, rate)


def _calibration_note(found: int, dismissed: int, accepted: int, rate: float | None) -> str:
    if found == 0:
        return "Nothing found yet."
    said = f"Found something {_times(found)}."
    if rate is None:
        return (
            f"{said} Too few to say how often it is wrong here "
            f"(at least {MIN_CALIBRATION_FINDINGS} are needed)."
        )
    parts = [said]
    if dismissed:
        parts.append(f"You said it was not a problem {_times(dismissed)} ({round(rate * 100)}%).")
    if accepted:
        parts.append(f"You kept it as written on purpose {_times(accepted)}.")
    if rate >= OFTEN_DISMISSED:
        parts.append(
            "It is often wrong for this story. It still runs: nothing changes unless you change it."
        )
    return " ".join(parts)


PASSAGE_WORDS = 8


def _first_words(text: str, start: Any, end: Any) -> str:
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end:
        return ""
    words = text[start:end].split()
    shown = " ".join(words[:PASSAGE_WORDS])
    return shown + ("…" if len(words) > PASSAGE_WORDS else "")


def _passage_words(
    coverage: dict[str, Any], texts: dict[str, str | None], scene: str | None
) -> None:
    """Each passage of a pass sent in groups or parts, with its first few words, read
    from the exact version the run checked (the job keeps only where each passage is)."""
    for key, sent in list(coverage.items()):
        plan_id, _, _pass = key.rpartition(":")
        text = texts.get(plan_id or scene or "")
        if not text or not isinstance(sent, dict):
            continue
        passages = sent.get("passages")
        if not isinstance(passages, list):
            continue
        # A copy: the job's own result is never changed.
        coverage[key] = {
            **sent,
            "passages": [
                {**item, "words": _first_words(text, item.get("start"), item.get("end"))}
                if isinstance(item, dict)
                else item
                for item in passages
            ],
        }


def _coverage(job: Job | None) -> dict[str, Any]:
    """How each pass of a check with the assistant was sent (CON-013), from its result."""
    if job is None or not isinstance(job.result, dict):
        return {}
    found: dict[str, Any] = {}
    for check_pass, outcome in (job.result.get("passes") or {}).items():
        if isinstance(outcome, dict) and isinstance(outcome.get("bundles"), dict):
            found[str(check_pass)] = outcome["bundles"]
    for scene in job.result.get("scenes") or []:
        for check_pass, outcome in (
            (scene.get("passes") or {}).items() if isinstance(scene, dict) else ()
        ):
            if isinstance(outcome, dict) and isinstance(outcome.get("bundles"), dict):
                found.setdefault(f"{scene.get('plan_id')}:{check_pass}", outcome["bundles"])
    return found


def _timing(check_pass: str, plan_id: str | None, outcome: Any) -> PassTiming | None:
    if not isinstance(outcome, dict):
        return None
    ms = outcome.get("ms")
    calls = outcome.get("calls")
    return PassTiming(
        check_pass=check_pass,
        plan_id=plan_id,
        ms=float(ms) if isinstance(ms, int | float) else None,
        calls=int(calls) if isinstance(calls, int) else 0,
        earlier=bool(outcome.get("earlier")),
    )


def _timings(job: Job | None) -> list[PassTiming]:
    """How long each pass took, from the job's result; older jobs kept no times."""
    if job is None or not isinstance(job.result, dict):
        return []
    found: list[PassTiming] = []
    for check_pass, outcome in (job.result.get("passes") or {}).items():
        if (timing := _timing(str(check_pass), None, outcome)) is not None:
            found.append(timing)
    for scene in job.result.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for check_pass, outcome in (scene.get("passes") or {}).items():
            plan_id = str(scene.get("plan_id") or "") or None
            if (timing := _timing(str(check_pass), plan_id, outcome)) is not None:
                found.append(timing)
    return found


RUN_KINDS: tuple[RunKind, ...] = ("checks", "assistant", "audit")

__all__ = [
    "MIN_CALIBRATION_FINDINGS",
    "calibration_note",
    "RUN_KINDS",
    "CalibrationLine",
    "ConsistencyRunService",
    "FindingView",
    "PassTiming",
    "PictureRead",
    "RunView",
    "compared_with",
    "picture_changes",
    "dependency_of",
]
