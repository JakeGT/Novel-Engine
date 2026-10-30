"""Checks kept as runs (CON-001): a scene's history of checks, one run, and an audit's run.

Each run says what it covered, the story's version and the text versions it read, the
passes it ran, the story's checking profile, which checks were worked out again and which
were reused because nothing they read had changed (CON-007), and, judged now, each finding
open, accepted, dismissed, superseded or fixed, and which scenes it is out of date for.
Reading a run never calls a model. ``/checks/calibration`` says how often the author
dismissed each check's findings (CON-016).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.studio import DismissalModel, _dismissal
from backend.container import Container
from backend.domain.check_fingerprints import BY_NAME
from backend.domain.check_passes import PASS_LABELS, CheckPass
from backend.domain.identity import AccessScope
from backend.services.consistency_runs import (
    CalibrationLine,
    ConsistencyRunService,
    PictureRead,
    RunView,
)
from backend.services.incremental_checks import STAGE_WORDS

router = APIRouter(prefix="/projects/{project_id}", tags=["checks"])


def get_runs(container: Container = Depends(get_container)) -> ConsistencyRunService:
    return container.consistency_runs  # type: ignore[no-any-return]


class RunPictureModel(BaseModel):
    """A picture a check with the assistant compared the scene with (CON-001)."""

    asset_id: str
    version_id: str
    label: str
    # send (the picture itself) or describe (its accepted description).
    choice: str
    # What has changed about it since, in plain words; null when it is as it was.
    changed: str | None = None


def _picture(item: PictureRead | None) -> RunPictureModel | None:
    if item is None:
        return None
    return RunPictureModel(
        asset_id=item.asset_id,
        version_id=item.version_id,
        label=item.label,
        choice=item.choice,
        changed=item.changed,
    )


class RunFindingModel(BaseModel):
    plan_id: str
    # "check": a check that needs no model; "note": the assistant's note.
    source: Literal["check", "note"]
    code: str
    severity: str
    message: str
    # The words it was about, from the version the run read.
    quoted: str = ""
    status: Literal["open", "accepted", "dismissed", "superseded", "fixed"]
    # What fixed it: its words changed, or the story did.
    fixed_by: Literal["words", "story"] | None = None
    dismissal: DismissalModel | None = None
    check_pass: str | None = None
    # Still open, but the scene changed since: it may no longer hold.
    stale: bool = False
    # Dismissed before, and back because what it says has changed since.
    came_back: bool = False
    # For a check that needs no model, the named check it belongs to (CON-007).
    check: str | None = None
    # For a note of the pictures pass, the picture it was compared with.
    picture: RunPictureModel | None = None


class CheckWorkModel(BaseModel):
    """One named check that needs no model, in one scene of a run."""

    name: str
    # Plain words for it.
    words: str
    # Worked out for this run, or reused because nothing it reads had changed.
    reused: bool
    # How long it took, in milliseconds (a reused one: looking it up); null for runs
    # kept before times were recorded (CON-001).
    ms: float | None = None


class StageModel(BaseModel):
    """One stage of the checks that need no model in one scene of a run (CON-001)."""

    stage: Literal["gather", "checks", "keep"]
    # Plain words for it.
    words: str
    ms: float


class PassTimingModel(BaseModel):
    """One pass of a check with the assistant: how long it took and the calls it made."""

    check_pass: str
    # Plain words for the pass.
    words: str
    # The scene, for an audit of more than one.
    plan_id: str | None = None
    ms: float | None = None
    calls: int = 0
    # An earlier run of the same job had already finished it: nothing was sent again.
    earlier: bool = False


class RunSceneModel(BaseModel):
    plan_id: str
    title: str
    in_plan: bool
    source_id: str | None = None
    version_id: str | None = None
    draft_id: str | None = None
    stale: bool
    # Why it is out of date, in plain words.
    reasons: list[str]
    open: int
    accepted: int = 0
    dismissed: int
    superseded: int = 0
    fixed: int
    # The checks that need no model, as this run worked them out (empty for older runs).
    checks: list[CheckWorkModel] = []
    # How long each stage of those checks took (empty for older runs).
    stages: list[StageModel] = []
    # The pictures the pictures pass was given for this scene (empty when it read none).
    pictures: list[RunPictureModel] = []


class RunModel(BaseModel):
    id: str
    kind: Literal["checks", "assistant", "audit"]
    scope: Literal["scene", "part", "story"]
    plan_id: str | None
    # The scene or part of the plan it covered, or "The whole story".
    title: str
    story_revision: int
    # "model_free" for the checks that need no model, then each assistant pass.
    passes: list[str]
    job_id: str | None
    # The job's status (queued, running, succeeded, failed, cancelled), or "done".
    status: str
    created_at: datetime | None
    stale: bool
    scenes: list[RunSceneModel]
    findings: list[RunFindingModel]
    # How much the story checked on its own then ("on_request", "balanced",
    # "thorough"), and the versions of the checks and of the grouping of passages.
    profile: dict[str, str] = {}
    # How each pass of a check with the assistant was sent, when it was sent in groups
    # (CON-013): the groups, whether every passage got every check, in plain words.
    coverage: dict[str, Any] = {}
    # How long each pass of a check with the assistant took (CON-001).
    timings: list[PassTimingModel] = []


class CalibrationModel(BaseModel):
    code: str
    words: str
    source: Literal["check", "note"]
    found: int
    dismissed: int
    accepted: int
    rate: float | None = None
    note: str


class CalibrationList(BaseModel):
    items: list[CalibrationModel]


def _checks(run_view: RunView, plan_id: str) -> list[CheckWorkModel]:
    kept = run_view.run.scene_fingerprints(plan_id)
    reused = set(kept.get("reused") or [])
    raw = kept.get("ms")
    took: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return [
        CheckWorkModel(
            name=name,
            words=BY_NAME[name].words,
            reused=name in reused,
            ms=float(took[name]) if isinstance(took.get(name), int | float) else None,
        )
        for name in sorted(kept.get("checks") or {})
        if name in BY_NAME
    ]


def _stages(run_view: RunView, plan_id: str) -> list[StageModel]:
    kept = run_view.run.scene_fingerprints(plan_id).get("stages")
    return [
        StageModel(stage=item["stage"], words=STAGE_WORDS[item["stage"]], ms=float(item["ms"]))
        for item in (kept if isinstance(kept, list) else [])
        if isinstance(item, dict)
        and item.get("stage") in STAGE_WORDS
        and isinstance(item.get("ms"), int | float)
    ]


def _pass_words(check_pass: str) -> str:
    try:
        return PASS_LABELS[CheckPass(check_pass)]
    except ValueError:
        return check_pass.replace("_", " ").capitalize()


def _calibration(line: CalibrationLine) -> CalibrationModel:
    return CalibrationModel(
        code=line.code,
        words=line.words,
        source=line.source,
        found=line.found,
        dismissed=line.dismissed,
        accepted=line.accepted,
        rate=line.rate,
        note=line.note,
    )


class RunList(BaseModel):
    items: list[RunModel]


def _run(view: RunView) -> RunModel:
    run = view.run
    return RunModel(
        id=run.id,
        kind=run.kind,
        scope=run.scope,
        plan_id=run.plan_id,
        title=view.title,
        story_revision=run.story_revision,
        passes=list(run.passes),
        job_id=run.job_id,
        status=view.status,
        created_at=run.created_at,
        stale=view.stale,
        scenes=[
            RunSceneModel(
                plan_id=part.input.plan_id,
                title=part.title,
                in_plan=part.in_plan,
                source_id=part.input.source_id,
                version_id=part.input.version_id,
                draft_id=part.input.draft_id,
                stale=part.staleness.stale,
                reasons=part.staleness.reasons(),
                open=part.tally.open,
                accepted=part.tally.accepted,
                dismissed=part.tally.dismissed,
                superseded=part.tally.superseded,
                fixed=part.tally.fixed,
                checks=_checks(view, part.input.plan_id),
                stages=_stages(view, part.input.plan_id),
                pictures=[
                    model
                    for picture in view.pictures.get(part.input.plan_id, [])
                    if (model := _picture(picture)) is not None
                ],
            )
            for part in view.scenes
        ],
        findings=[
            RunFindingModel(
                plan_id=item.plan_id,
                source=item.source,
                code=item.code,
                severity=item.severity,
                message=item.message,
                quoted=item.quoted,
                status=item.status,
                fixed_by=item.fixed_by,
                dismissal=_dismissal(item.dismissal),
                check_pass=item.check_pass,
                stale=item.stale,
                came_back=item.came_back,
                check=item.check,
                picture=_picture(item.picture),
            )
            for item in view.findings
        ],
        profile={str(k): str(v) for k, v in run.profile.items()},
        coverage=view.coverage,
        timings=[
            PassTimingModel(
                check_pass=item.check_pass,
                words=_pass_words(item.check_pass),
                plan_id=item.plan_id,
                ms=item.ms,
                calls=item.calls,
                earlier=item.earlier,
            )
            for item in view.timings
        ],
    )


@router.get("/plan/{plan_id}/checks/runs")
async def scene_runs(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(get_project_scope),
    runs: ConsistencyRunService = Depends(get_runs),
) -> RunList:
    """The checks that read this scene, newest first (audits included), judged for it."""
    found = await runs.history(scope, scope.workspace_id or "", project_id, plan_id)
    return RunList(items=[_run(view) for view in found])


@router.post("/plan/{plan_id}/checks/runs", status_code=status.HTTP_201_CREATED)
async def record_scene_run(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(require_project_scope),
    runs: ConsistencyRunService = Depends(get_runs),
) -> RunModel:
    """Keep a record of what the checks that need no model find in the scene now. Free;
    when nothing changed since the last record, that record is returned."""
    workspace_id = scope.workspace_id or ""
    run = await runs.record_checks(scope, workspace_id, project_id, plan_id, asked=True)
    assert run is not None
    return _run(await runs.get(scope, workspace_id, project_id, run.id))


@router.get("/checks/runs/{run_id}")
async def get_run(
    project_id: str,
    run_id: str,
    scope: AccessScope = Depends(get_project_scope),
    runs: ConsistencyRunService = Depends(get_runs),
) -> RunModel:
    return _run(await runs.get(scope, scope.workspace_id or "", project_id, run_id))


@router.get("/checks/runs/by-job/{job_id}")
async def run_for_job(
    project_id: str,
    job_id: str,
    scope: AccessScope = Depends(get_project_scope),
    runs: ConsistencyRunService = Depends(get_runs),
) -> RunModel:
    """The run an audit or a check with the assistant keeps, once it has started reading."""
    return _run(await runs.for_job(scope, scope.workspace_id or "", project_id, job_id))


@router.get("/checks/calibration")
async def calibration(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    runs: ConsistencyRunService = Depends(get_runs),
) -> CalibrationList:
    """How often each check's findings were dismissed in this line of the story: a note
    for the author; nothing about the checks changes because of it."""
    found = await runs.calibration(scope, scope.workspace_id or "", project_id)
    return CalibrationList(items=[_calibration(line) for line in found])
