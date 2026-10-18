"""Checks kept as runs (CON-001): a scene's history of checks, one run, and an audit's run.

Each run says what it covered, the story's version and the text versions it read, the
passes it ran, and, judged now, each finding open, dismissed or fixed, and which scenes it
is out of date for. Reading a run never calls a model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.studio import DismissalModel, _dismissal
from backend.container import Container
from backend.domain.identity import AccessScope
from backend.services.consistency_runs import ConsistencyRunService, RunView

router = APIRouter(prefix="/projects/{project_id}", tags=["checks"])


def get_runs(container: Container = Depends(get_container)) -> ConsistencyRunService:
    return container.consistency_runs  # type: ignore[no-any-return]


class RunFindingModel(BaseModel):
    plan_id: str
    # "check": a check that needs no model; "note": the assistant's note.
    source: Literal["check", "note"]
    code: str
    severity: str
    message: str
    # The words it was about, from the version the run read.
    quoted: str = ""
    status: Literal["open", "dismissed", "fixed"]
    # What fixed it: its words changed, or the story did.
    fixed_by: Literal["words", "story"] | None = None
    dismissal: DismissalModel | None = None
    check_pass: str | None = None
    # Still open, but the scene changed since: it may no longer hold.
    stale: bool = False


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
    dismissed: int
    fixed: int


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
                dismissed=part.tally.dismissed,
                fixed=part.tally.fixed,
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
            )
            for item in view.findings
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
