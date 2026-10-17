"""What the assistant would be given for a scene, and the author's say over it (ADR-021)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain.context import SECTION_TITLES, ContextItem, ContextSettings
from backend.domain.identity import AccessScope
from backend.services.context_service import ContextService, SceneContext

router = APIRouter(prefix="/projects/{project_id}", tags=["context"])


def get_context_service(container: Container = Depends(get_container)) -> ContextService:
    return container.context_service  # type: ignore[no-any-return]


class ContextItemModel(BaseModel):
    key: str
    section: str
    section_title: str
    text: str
    reason: str
    authority: str
    record_type: str
    record_id: str
    location: str | None = None
    words: int
    score: float
    hard: bool
    pinned: bool


class LeftOutModel(BaseModel):
    item: ContextItemModel
    # excluded (the author said so), budget (no room), secret (not known to the viewpoint).
    reason: str


class IndexStatusModel(BaseModel):
    texts: int
    indexed: int
    behind: list[str]
    names_as_of: int | None = None
    semantic: bool


class SceneContextModel(BaseModel):
    scene_id: str
    point_of_view: str | None = None
    settings: ContextSettings
    policy_version: str
    budget: int
    words: int
    checksum: str
    items: list[ContextItemModel]
    left_out: list[LeftOutModel]
    index: IndexStatusModel | None = None
    # The context exactly as it would be given, for anyone who wants to read it whole.
    rendered: str


class ContextSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pins: list[str] = Field(default_factory=list, max_length=200)
    excludes: list[str] = Field(default_factory=list, max_length=200)
    budget: int | None = Field(default=None, ge=0, le=100_000)
    include_secrets: bool = False
    expected_revision: int = Field(ge=0)


class RebuildResult(BaseModel):
    passages: int


def _item(item: ContextItem, pinned: set[str]) -> ContextItemModel:
    return ContextItemModel(
        key=item.key,
        section=item.section.value,
        section_title=SECTION_TITLES[item.section],
        text=item.text,
        reason=item.reason,
        authority=item.authority,
        record_type=item.record_type,
        record_id=item.record_id,
        location=item.location,
        words=item.words,
        score=round(item.score, 3),
        hard=item.hard,
        pinned=item.key in pinned,
    )


def _model(context: SceneContext) -> SceneContextModel:
    manifest = context.manifest
    index = context.index
    return SceneContextModel(
        scene_id=context.scene.id,
        point_of_view=context.point_of_view,
        settings=context.settings,
        policy_version=manifest.policy_version,
        budget=manifest.budget,
        words=manifest.words,
        checksum=manifest.checksum,
        items=[_item(item, manifest.pinned) for item in manifest.items],
        left_out=[
            LeftOutModel(item=_item(omission.item, manifest.pinned), reason=omission.reason.value)
            for omission in manifest.left_out
        ],
        index=IndexStatusModel(
            texts=index.texts,
            indexed=index.indexed,
            behind=index.behind,
            names_as_of=index.names_as_of,
            semantic=index.semantic,
        )
        if index is not None
        else None,
        rendered=manifest.rendered,
    )


@router.get("/plan/{plan_id}/context")
async def scene_context(
    project_id: str,
    plan_id: str,
    budget: int | None = Query(default=None, ge=0, le=100_000),
    secrets: bool | None = None,
    scope: AccessScope = Depends(get_project_scope),
    service: ContextService = Depends(get_context_service),
) -> SceneContextModel:
    context = await service.for_scene(
        scope, scope.workspace_id or "", project_id, plan_id, budget=budget, include_secrets=secrets
    )
    return _model(context)


@router.put("/plan/{plan_id}/context")
async def set_scene_context(
    project_id: str,
    plan_id: str,
    body: ContextSettingsRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: ContextService = Depends(get_context_service),
) -> SceneContextModel:
    await service.set_settings(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        settings=ContextSettings(
            pins=body.pins,
            excludes=body.excludes,
            budget=body.budget,
            include_secrets=body.include_secrets,
        ),
        expected_revision=body.expected_revision,
    )
    return _model(await service.for_scene(scope, scope.workspace_id or "", project_id, plan_id))


@router.get("/texts-index")
async def writing_index(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    container: Container = Depends(get_container),
) -> IndexStatusModel:
    status = await container.passage_index.status(scope.workspace_id or "", project_id)
    return IndexStatusModel(
        texts=status.texts,
        indexed=status.indexed,
        behind=status.behind,
        names_as_of=status.names_as_of,
        semantic=status.semantic,
    )


@router.post("/texts-index/rebuild")
async def rebuild_writing_index(
    project_id: str,
    scope: AccessScope = Depends(require_project_scope),
    container: Container = Depends(get_container),
) -> RebuildResult:
    """Index every text again, finding names with the story as it is now.

    The write scope already requires permission to change the project.
    """
    count = await container.passage_index.rebuild(scope.workspace_id or "", project_id)
    return RebuildResult(passages=count)
