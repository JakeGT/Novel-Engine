"""The story plan: an outline of intent, kept apart from what is true in the story."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_plan_service
from backend.domain.arcs import MAX_STAGES, ArcStage, arc_path
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
    # For an arc: how its character changes, as stages in order (STY-006).
    path: list[ArcStage] = Field(default_factory=list)
    created_revision: int


class OpenItemModel(BaseModel):
    plan_id: str
    code: str
    # In an author's words: "This scene needs a place."
    message: str
    # gap: not filled in yet. problem: out of order, or not ready where it comes.
    kind: str = "gap"


class ConditionStateModel(BaseModel):
    plan_id: str
    # before or after.
    when: str
    # In ordinary words: "Mara must have this: it is located at Lantern Harbor."
    reading: str
    # Whether the story bible says it is true where the scene comes (at the scene's
    # story time, if it has one). Null when that cannot be told.
    holds: bool | None
    # hard (required) or soft (preferred).
    strength: str = "hard"
    # met, planned, not_met, unclear or unchecked (written in words).
    status: str = "unclear"
    # Why, in plain words, when there is more to say than the status.
    note: str = ""
    # Its place in the scene's own list of conditions.
    index: int = 0
    # The earlier scene meant to bring it about or undo it, if any.
    planned_in: str | None = None


class PromiseModel(BaseModel):
    """A thread opened or a fact hinted: something the reader is owed (STY-007)."""

    # thread or clue.
    kind: str
    # The thread's plan id, or the hinted fact's id.
    subject_id: str
    # Where it is first made, and its scene number in reading order.
    made_in: str
    made_at: int
    # Where it is moved along, or hinted again, before it is kept.
    moved_along: list[str]
    # Where it is kept; null while it is still owed.
    kept_in: str | None
    kept_at: int | None
    # Scenes between the promise and its keeping; while owed, scenes since it was made.
    distance: int
    # Kept before it is made: resolved before it is opened.
    out_of_order: bool


class ThreadStepModel(BaseModel):
    plan_id: str
    # opens, advances or resolves.
    step: str


class ThreadProgressModel(BaseModel):
    thread_id: str
    # Where the thread opens, moves along and resolves, in outline order.
    steps: list[ThreadStepModel]


class ArcStageReadingModel(BaseModel):
    # Its place in the arc's own order; the first stage is where the character starts.
    index: int
    name: str
    # The scene where it is reached, and that scene's number in reading order.
    scene_id: str | None
    scene_number: int | None
    fact_id: str | None
    # How its fact stands where it is reached: holds, not_then, gone or unclear.
    fact: str | None


class ArcReadingModel(BaseModel):
    arc_id: str
    # Whose arc it is.
    about: list[str]
    stages: list[ArcStageReadingModel]


class OutlineModel(BaseModel):
    items: list[PlanItemModel]
    # What the plan still needs. Derived; a plan with gaps is still a valid plan.
    open: list[OpenItemModel]
    # Each scene's conditions checked against the story now. Derived; never blocks.
    conditions: list[ConditionStateModel]
    # Each thread's steps through the plan. Derived.
    threads: list[ThreadProgressModel] = Field(default_factory=list)
    # Threads opened and facts hinted, in the order the reader meets them. Derived.
    promises: list[PromiseModel] = Field(default_factory=list)
    # Each arc's stages, where each is reached and whether its fact holds. Derived.
    arcs: list[ArcReadingModel] = Field(default_factory=list)


class SetPathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[ArcStage] = Field(max_length=MAX_STAGES)
    expected_revision: int = Field(ge=0)


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
    # For an arc only: how its character changes, stage by stage.
    path: list[ArcStage] = Field(default_factory=list, max_length=MAX_STAGES)
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
        path=arc_path(item.details),
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
    reading = await service.outline(scope, scope.workspace_id or "", project_id)
    items = reading.items
    return OutlineModel(
        items=[_model(item) for item in items],
        open=[
            OpenItemModel(
                plan_id=item.plan_id, code=item.code, message=item.message, kind=item.kind
            )
            for item in reading.open
        ],
        conditions=[
            ConditionStateModel(
                plan_id=state.plan_id,
                when=state.when,
                reading=state.reading,
                holds=state.holds,
                strength=state.strength,
                status=state.status,
                note=state.note,
                index=state.index,
                planned_in=state.planned_in,
            )
            for state in reading.conditions
        ],
        threads=[
            ThreadProgressModel(
                thread_id=thread_id,
                steps=[ThreadStepModel(plan_id=s.plan_id, step=s.step) for s in steps],
            )
            for thread_id, steps in sorted(thread_progress(items).items())
        ],
        promises=[
            PromiseModel(
                kind=promise.kind,
                subject_id=promise.subject_id,
                made_in=promise.made_in,
                made_at=promise.made_at,
                moved_along=promise.moved_along,
                kept_in=promise.kept_in,
                kept_at=promise.kept_at,
                distance=promise.distance,
                out_of_order=promise.out_of_order,
            )
            for promise in reading.promises
        ],
        arcs=[
            ArcReadingModel(
                arc_id=arc.arc_id,
                about=arc.about,
                stages=[
                    ArcStageReadingModel(
                        index=stage.index,
                        name=stage.name,
                        scene_id=stage.scene_id,
                        scene_number=stage.scene_number,
                        fact_id=stage.fact_id,
                        fact=stage.fact,
                    )
                    for stage in arc.stages
                ],
            )
            for arc in reading.arcs
        ],
    )


@router.put("/{plan_id}/path", response_model=PlanItemModel)
async def set_arc_path(
    project_id: str,
    plan_id: str,
    body: SetPathRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: PlanService = Depends(get_plan_service),
) -> PlanItemModel:
    """Say how an arc's character changes: its stages in order, each with its scene."""
    result, item = await service.set_path(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        stages=body.stages,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(item)


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
        path=body.path,
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
