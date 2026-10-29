"""The assistant's help with the plan: a scene's intent, bridges, an arc's stages and
gaps (STY-003, STY-004, STY-006).

Each kind of help has an estimate, to show the cost before anything is sent, and a
start, which queues a job. A finished job's result names the suggested plan change,
which waits in review; nothing in the plan changes by asking.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.assistant_settings import confirmed_tokens
from backend.api.v1.studio import JobModel, _job
from backend.container import Container
from backend.domain.arcs import MAX_STAGES
from backend.domain.identity import AccessScope
from backend.domain.scene_conditions import MAX_CONDITIONS
from backend.services.planning_help import PlanningHelpService

router = APIRouter(prefix="/projects/{project_id}/plan", tags=["plan-help"])


def get_planning_help(container: Container = Depends(get_container)) -> PlanningHelpService:
    return container.planning_help  # type: ignore[no-any-return]


class HelpEstimateModel(BaseModel):
    # About how many tokens the request would use, reading and answering.
    total_tokens: int
    # Whether the scene's written words are read too.
    reads_prose: bool


@router.get("/{plan_id}/intent/estimate", response_model=HelpEstimateModel)
async def intent_estimate(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(get_project_scope),
    help_: PlanningHelpService = Depends(get_planning_help),
) -> HelpEstimateModel:
    """What suggesting this scene's intent would use, before anything is sent. A read,
    so no CSRF token; only someone who may change the plan is answered."""
    found = await help_.intent_estimate(scope, scope.workspace_id or "", project_id, plan_id)
    return HelpEstimateModel(**found)


@router.post(
    "/{plan_id}/intent/suggest", response_model=JobModel, status_code=status.HTTP_202_ACCEPTED
)
async def suggest_intent(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(require_project_scope),
    help_: PlanningHelpService = Depends(get_planning_help),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Suggest what the viewpoint character wants, what is in the way and how it ends,
    from the plan card and the prose, as a plan change for review."""
    job = await help_.start_intent(
        scope, scope.workspace_id or "", project_id, plan_id, confirmed_tokens=confirmed
    )
    return _job(job)


@router.get("/{plan_id}/bridges/estimate", response_model=HelpEstimateModel)
async def bridge_estimate(
    project_id: str,
    plan_id: str,
    condition: int = Query(ge=0, lt=MAX_CONDITIONS),
    scope: AccessScope = Depends(get_project_scope),
    help_: PlanningHelpService = Depends(get_planning_help),
) -> HelpEstimateModel:
    """What suggesting bridge scenes for one unmet condition would use."""
    found = await help_.bridge_estimate(
        scope, scope.workspace_id or "", project_id, plan_id, condition
    )
    return HelpEstimateModel(**found)


@router.post(
    "/{plan_id}/bridges/suggest", response_model=JobModel, status_code=status.HTTP_202_ACCEPTED
)
async def suggest_bridges(
    project_id: str,
    plan_id: str,
    condition: int = Query(ge=0, lt=MAX_CONDITIONS),
    scope: AccessScope = Depends(require_project_scope),
    help_: PlanningHelpService = Depends(get_planning_help),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Suggest one or two scenes to come before this one and bring about what it needs
    first, as a plan change for review."""
    job = await help_.start_bridges(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        condition,
        confirmed_tokens=confirmed,
    )
    return _job(job)


# ---------------------------------------------------------------- an arc's stages (STY-006)


@router.get("/{plan_id}/stages/estimate", response_model=HelpEstimateModel)
async def stages_estimate(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(get_project_scope),
    help_: PlanningHelpService = Depends(get_planning_help),
) -> HelpEstimateModel:
    """What suggesting the stages of an arc with none yet would use."""
    found = await help_.arc_estimate(scope, scope.workspace_id or "", project_id, plan_id)
    return HelpEstimateModel(**found)


@router.post(
    "/{plan_id}/stages/suggest", response_model=JobModel, status_code=status.HTTP_202_ACCEPTED
)
async def suggest_stages(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(require_project_scope),
    help_: PlanningHelpService = Depends(get_planning_help),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Suggest the stages of an arc from the arc, the story bible and the plan's scenes,
    as a plan change for review. Only an arc with no stages yet."""
    job = await help_.start_arc(
        scope, scope.workspace_id or "", project_id, plan_id, confirmed_tokens=confirmed
    )
    return _job(job)


# ---------------------------------------------------------------- gaps (STY-004)


@router.get("/{plan_id}/gap/estimate", response_model=HelpEstimateModel)
async def gap_estimate(
    project_id: str,
    plan_id: str,
    stage: int | None = Query(default=None, ge=1, lt=MAX_STAGES),
    scope: AccessScope = Depends(get_project_scope),
    help_: PlanningHelpService = Depends(get_planning_help),
) -> HelpEstimateModel:
    """What suggesting a scene for a gap would use: a thread nothing moves along (the
    thread's id), or an arc's stage no scene reaches (the arc's id and ``stage``)."""
    found = await help_.gap_estimate(scope, scope.workspace_id or "", project_id, plan_id, stage)
    return HelpEstimateModel(**found)


@router.post(
    "/{plan_id}/gap/suggest", response_model=JobModel, status_code=status.HTTP_202_ACCEPTED
)
async def suggest_gap(
    project_id: str,
    plan_id: str,
    stage: int | None = Query(default=None, ge=1, lt=MAX_STAGES),
    scope: AccessScope = Depends(require_project_scope),
    help_: PlanningHelpService = Depends(get_planning_help),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Suggest one scene that fills a gap in the plan, as a plan change for review."""
    job = await help_.start_gap(
        scope, scope.workspace_id or "", project_id, plan_id, stage, confirmed_tokens=confirmed
    )
    return _job(job)
