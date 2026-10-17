"""The story plan: an outline of intent, kept apart from what is true in the story."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_plan_service
from backend.domain.canon.plans import (
    MAX_SUMMARY_LENGTH,
    MAX_TITLE_LENGTH,
    PlanItem,
    PlanKind,
    PlanRef,
)
from backend.domain.identity import AccessScope
from backend.domain.knowledge import MAX_REVEALS, Reveal, scene_reveals, scene_time
from backend.domain.scene_conditions import (
    MAX_CONDITIONS,
    SceneCondition,
    plain_details,
    scene_conditions,
)
from backend.domain.scene_intent import SceneIntent, scene_intent
from backend.domain.threads import thread_advances, thread_progress
from backend.domain.worldtime import WorldTime
from backend.services.plan_service import PlanService

router = APIRouter(prefix="/projects/{project_id}/plan", tags=["plan"])


class PlanRefModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1, max_length=200)
    # point_of_view, place, participant or about.
    role: str = Field(min_length=1, max_length=40)


class PlanItemModel(BaseModel):
    id: str
    # story, arc, beat, scene or thread.
    kind: str
    title: str
    # What is meant to happen. Intent: never a record of what is true in the story.
    summary: str
    parent_id: str | None
    position: int
    refs: list[PlanRefModel]
    sets_up: list[str]
    pays_off: list[str]
    # Threads it moves along between their opening and their resolution.
    advances: list[str] = Field(default_factory=list)
    details: dict[str, str]
    # For a scene: what its viewpoint character wants, what stands in the way, how it ends.
    intent: SceneIntent | None = None
    # For a scene: what must be true before it, and what it should leave true after it.
    conditions: list[SceneCondition]
    # For a scene: the facts it shows the reader, plainly or as a hint.
    reveals: list[Reveal] = Field(default_factory=list)
    # For a scene: when in story time it happens, if the author has said.
    when: WorldTime | None = None
    created_revision: int


class OpenItemModel(BaseModel):
    plan_id: str
    code: str
    # In an author's words: "This scene needs a place."
    message: str


class ConditionStateModel(BaseModel):
    plan_id: str
    # before or after.
    when: str
    # In ordinary words: "Mara must have this: it is located at Lantern Harbor."
    reading: str
    # Whether it is true in the story as it stands. Null when its entry is gone.
    holds: bool | None


class ThreadStepModel(BaseModel):
    plan_id: str
    # opens, advances or resolves.
    step: str


class ThreadProgressModel(BaseModel):
    thread_id: str
    # Where the thread opens, moves along and resolves, in outline order.
    steps: list[ThreadStepModel]


class OutlineModel(BaseModel):
    items: list[PlanItemModel]
    # What the plan still needs. Derived; a plan with gaps is still a valid plan.
    open: list[OpenItemModel]
    # Each scene's conditions checked against the story now. Derived; never blocks.
    conditions: list[ConditionStateModel]
    # Each thread's steps through the plan. Derived.
    threads: list[ThreadProgressModel] = Field(default_factory=list)


class SetConditionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conditions: list[SceneCondition] = Field(max_length=MAX_CONDITIONS)
    expected_revision: int = Field(ge=0)


class SetRevealsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reveals: list[Reveal] = Field(max_length=MAX_REVEALS)
    expected_revision: int = Field(ge=0)


class SetSceneTimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Null, or an unknown time, clears it.
    when: WorldTime | None = None
    expected_revision: int = Field(ge=0)


class AddPlanItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PlanKind
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    summary: str = Field(default="", max_length=MAX_SUMMARY_LENGTH)
    parent_id: str | None = Field(default=None, max_length=200)
    refs: list[PlanRefModel] = Field(default_factory=list, max_length=40)
    sets_up: list[str] = Field(default_factory=list, max_length=20)
    pays_off: list[str] = Field(default_factory=list, max_length=20)
    advances: list[str] = Field(default_factory=list, max_length=20)
    details: dict[str, str] = Field(default_factory=dict)
    # For a scene only.
    intent: SceneIntent | None = None
    expected_revision: int = Field(ge=0)


class ChangePlanItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Anything left out stays as it is; a list or mapping replaces the whole of it.
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY_LENGTH)
    refs: list[PlanRefModel] | None = Field(default=None, max_length=40)
    sets_up: list[str] | None = Field(default=None, max_length=20)
    pays_off: list[str] | None = Field(default=None, max_length=20)
    advances: list[str] | None = Field(default=None, max_length=20)
    details: dict[str, str] | None = None
    # For a scene only; an empty one clears it.
    intent: SceneIntent | None = None
    expected_revision: int = Field(ge=0)


class MovePlanItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # What it should sit under; null puts it at the top level.
    parent_id: str | None = Field(default=None, max_length=200)
    # The item it should come just before; null puts it last.
    before_id: str | None = Field(default=None, max_length=200)
    expected_revision: int = Field(ge=0)


class RemovePlanItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class RemovedModel(BaseModel):
    revision: int
    # How many parts of the plan went, counting everything under it.
    removed: int


def _model(item: PlanItem) -> PlanItemModel:
    return PlanItemModel(
        id=item.id,
        kind=item.kind.value,
        title=item.title,
        summary=item.summary,
        parent_id=item.parent_id,
        position=item.position,
        refs=[PlanRefModel(entity_id=ref.entity_id, role=ref.role) for ref in item.refs],
        sets_up=item.sets_up,
        pays_off=item.pays_off,
        advances=thread_advances(item.details),
        details=plain_details(item.details),
        intent=scene_intent(item.details),
        conditions=scene_conditions(item.details),
        reveals=scene_reveals(item.details),
        when=scene_time(item.details),  # type: ignore[arg-type]
        created_revision=item.created_revision,
    )


def _refs(refs: list[PlanRefModel] | None) -> list[PlanRef] | None:
    if refs is None:
        return None
    return [PlanRef(entity_id=ref.entity_id, role=ref.role) for ref in refs]


@router.get("", response_model=OutlineModel)
async def get_outline(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> OutlineModel:
    items, still_open = await service.outline(scope, scope.workspace_id or "", project_id)
    states = await service.conditions(scope, scope.workspace_id or "", project_id)
    return OutlineModel(
        items=[_model(item) for item in items],
        open=[
            OpenItemModel(plan_id=item.plan_id, code=item.code, message=item.message)
            for item in still_open
        ],
        conditions=[
            ConditionStateModel(
                plan_id=state.plan_id, when=state.when, reading=state.reading, holds=state.holds
            )
            for state in states
        ],
        threads=[
            ThreadProgressModel(
                thread_id=thread_id,
                steps=[ThreadStepModel(plan_id=s.plan_id, step=s.step) for s in steps],
            )
            for thread_id, steps in sorted(thread_progress(items).items())
        ],
    )


@router.put("/{plan_id}/conditions", response_model=PlanItemModel)
async def set_scene_conditions(
    project_id: str,
    plan_id: str,
    body: SetConditionsRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> PlanItemModel:
    """Say what must be true before a scene and what it should leave true after it."""
    result, item = await service.set_conditions(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        conditions=body.conditions,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(item)


@router.put("/{plan_id}/reveals", response_model=PlanItemModel)
async def set_scene_reveals(
    project_id: str,
    plan_id: str,
    body: SetRevealsRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> PlanItemModel:
    """Say which facts a scene shows the reader."""
    result, item = await service.set_reveals(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        reveals=body.reveals,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(item)


@router.put("/{plan_id}/when", response_model=PlanItemModel)
async def set_scene_when(
    project_id: str,
    plan_id: str,
    body: SetSceneTimeRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> PlanItemModel:
    """Say when in story time a scene happens."""
    result, item = await service.set_scene_time(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        when=body.when,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(item)


@router.post("", response_model=PlanItemModel, status_code=201)
async def add_plan_item(
    project_id: str,
    body: AddPlanItemRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> PlanItemModel:
    result, item = await service.add(
        scope,
        scope.workspace_id or "",
        project_id,
        kind=body.kind,
        title=body.title,
        summary=body.summary,
        parent_id=body.parent_id,
        refs=_refs(body.refs),
        sets_up=body.sets_up,
        pays_off=body.pays_off,
        details=body.details,
        advances=body.advances,
        intent=body.intent,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(item)


@router.patch("/{plan_id}", response_model=PlanItemModel)
async def change_plan_item(
    project_id: str,
    plan_id: str,
    body: ChangePlanItemRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> PlanItemModel:
    result, item = await service.change(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        title=body.title,
        summary=body.summary,
        refs=_refs(body.refs),
        sets_up=body.sets_up,
        pays_off=body.pays_off,
        details=body.details,
        advances=body.advances,
        intent=body.intent,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(item)


@router.post("/{plan_id}/move", response_model=PlanItemModel)
async def move_plan_item(
    project_id: str,
    plan_id: str,
    body: MovePlanItemRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> PlanItemModel:
    result, item = await service.move(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        parent_id=body.parent_id,
        before_id=body.before_id,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(item)


@router.post("/{plan_id}/remove", response_model=RemovedModel)
async def remove_plan_item(
    project_id: str,
    plan_id: str,
    body: RemovePlanItemRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> RemovedModel:
    result, removed = await service.remove(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return RemovedModel(revision=result.revision, removed=removed)


__all__: list[Any] = ["router"]
