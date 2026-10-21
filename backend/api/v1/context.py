"""What the assistant would be given for a scene, and the author's say over it (ADR-021)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.assistant_settings import confirmed_tokens
from backend.api.v1.studio import JobModel, _job
from backend.container import Container
from backend.domain.context import (
    SHOWN_SECTION_TITLES,
    ContextComparison,
    ContextItem,
    ContextSettings,
    SceneSummary,
    picture_record,
)
from backend.domain.identity import AccessScope
from backend.services.context_service import ContextService, SceneContext
from backend.services.scene_summaries import (
    MAX_SUMMARY_WORDS,
    SceneSummaryService,
    SummaryView,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["context"])


def get_context_service(container: Container = Depends(get_container)) -> ContextService:
    return container.context_service  # type: ignore[no-any-return]


def get_summaries(container: Container = Depends(get_container)) -> SceneSummaryService:
    return container.scene_summaries  # type: ignore[no-any-return]


class SignalModel(BaseModel):
    """One part of why an item ranks where it does (CTX-007)."""

    # importance, continuity, distance, diversity, meaning, thread, out of date
    name: str
    # How much it moved the item's score; an item's signals add up to its score.
    effect: float
    note: str


class ContextPictureModel(BaseModel):
    """A picture in the context: which version, and how it is meant to reach the model
    (CTX-013). Whether it actually went as a picture depends on the assistant used, and
    is recorded with each request."""

    asset_id: str
    version_id: str
    # send, describe or leave_out: the author's choice for the picture.
    choice: str
    # picture, description, named or left_out: what the choice means here.
    representation: str
    representation_words: str


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
    picture: ContextPictureModel | None = None
    signals: list[SignalModel] = Field(default_factory=list)


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
    # Texts changed and not yet read into the index (CTX-005), since how many seconds,
    # and how many of them failed at least once and wait to be tried again.
    waiting: int = 0
    waiting_seconds: float | None = None
    failing: int = 0


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


class KeptContextModel(BaseModel):
    """The context a kept version of the scene's prose was drafted from."""

    manifest_id: str
    version_id: str
    version_number: int
    kept_at: datetime
    words: int


class RemovedContextModel(BaseModel):
    key: str
    section_title: str | None = None
    # The words given then. Null for a passage of the author's writing, or a context kept
    # before words were recorded.
    text: str | None = None
    reason: str
    words: int
    # Why it is not given now: excluded, budget or secret. Null when it is no longer found.
    left_out: str | None = None
    # The item as it would be now, when it is still found but left out.
    now: ContextItemModel | None = None


class ChangedContextModel(BaseModel):
    item: ContextItemModel
    text_then: str | None = None
    reason_then: str
    section_title_then: str | None = None
    # What differs: words, section, reason.
    what: list[str]


class ContextComparisonModel(BaseModel):
    manifest_id: str
    kept_at: datetime
    words_then: int
    words_now: int
    budget_then: int
    budget_now: int
    # The rules for putting the context together changed since it was kept.
    rules_changed: bool
    # Exactly the same context as was given then.
    same: bool
    added: list[ContextItemModel]
    removed: list[RemovedContextModel]
    changed: list[ChangedContextModel]
    unchanged: int


def _item(item: ContextItem, pinned: set[str]) -> ContextItemModel:
    return ContextItemModel(
        key=item.key,
        section=item.section.value,
        section_title=SHOWN_SECTION_TITLES[item.section],
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
        picture=_picture(item),
        signals=[
            SignalModel(name=signal.name, effect=round(signal.effect, 3), note=signal.note)
            for signal in item.signals
        ],
    )


def _picture(item: ContextItem) -> ContextPictureModel | None:
    if item.media is None:
        return None
    record = picture_record(item.media)
    return ContextPictureModel(
        asset_id=record["asset_id"],
        version_id=record["version_id"],
        choice=record["choice"],
        representation=record["representation"],
        representation_words=record["representation_words"],
    )


def _index(index: Any) -> IndexStatusModel:
    return IndexStatusModel(
        texts=index.texts,
        indexed=index.indexed,
        behind=index.behind,
        names_as_of=index.names_as_of,
        semantic=index.semantic,
        waiting=index.waiting,
        waiting_seconds=round(index.waiting_seconds, 1)
        if index.waiting_seconds is not None
        else None,
        failing=index.failing,
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
        index=_index(index) if index is not None else None,
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


_WHAT = {"the words": "words", "where it sits": "section", "why it is given": "reason"}


def _comparison(result: ContextComparison) -> ContextComparisonModel:
    kept, now = result.kept, result.now
    return ContextComparisonModel(
        manifest_id=kept.id,
        kept_at=kept.created_at,
        words_then=kept.words,
        words_now=now.words,
        budget_then=kept.budget,
        budget_now=now.budget,
        rules_changed=kept.policy_version != now.policy_version,
        same=result.same,
        added=[_item(item, set()) for item in result.added],
        removed=[
            RemovedContextModel(
                key=then.key,
                section_title=SHOWN_SECTION_TITLES[then.section] if then.section else None,
                text=then.text,
                reason=then.reason,
                words=then.words,
                left_out=omission.reason.value if omission else None,
                now=_item(omission.item, set()) if omission else None,
            )
            for then, omission in result.removed
        ],
        changed=[
            ChangedContextModel(
                item=_item(change.now, set()),
                text_then=change.then.text,
                reason_then=change.then.reason,
                section_title_then=SHOWN_SECTION_TITLES[change.then.section]
                if change.then.section
                else None,
                what=[_WHAT[what] for what in change.what],
            )
            for change in result.changed
        ],
        unchanged=result.unchanged,
    )


@router.get("/plan/{plan_id}/context/kept")
async def kept_scene_contexts(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: ContextService = Depends(get_context_service),
) -> list[KeptContextModel]:
    """The contexts kept versions of this scene were drafted from, newest first."""
    kept = await service.kept_for_scene(scope, scope.workspace_id or "", project_id, plan_id)
    return [
        KeptContextModel(
            manifest_id=item.manifest_id,
            version_id=item.version_id,
            version_number=item.version_number,
            kept_at=item.kept_at,
            words=item.words,
        )
        for item in kept
    ]


@router.get("/plan/{plan_id}/context/kept/{manifest_id}")
async def compare_scene_context(
    project_id: str,
    plan_id: str,
    manifest_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: ContextService = Depends(get_context_service),
) -> ContextComparisonModel:
    """What the assistant would use now against what it used for a kept draft."""
    result = await service.compare_kept(
        scope, scope.workspace_id or "", project_id, plan_id, manifest_id
    )
    return _comparison(result)


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
    return _index(status)


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


# ------------------------------------------------------------ summaries (CTX-006)


class SceneSummaryModel(BaseModel):
    id: str
    body: str
    # proposed, accepted, rejected, replaced
    state: str
    # author, model (suggested by the assistant), model_corrected (suggested, then corrected)
    origin: str
    version_id: str
    created_at: datetime
    model: str | None = None
    prompt_version: str | None = None
    job_id: str | None = None
    decided_at: datetime | None = None


class SceneSummariesModel(BaseModel):
    plan_id: str
    has_prose: bool
    version_id: str | None = None
    version_number: int | None = None
    # What the plan card says happens, used for this scene when nothing is kept.
    plan_card: str | None = None
    kept: SceneSummaryModel | None = None
    # The kept summary is of an earlier version of the prose.
    kept_out_of_date: bool = False
    suggested: list[SceneSummaryModel] = Field(default_factory=list)
    max_words: int = MAX_SUMMARY_WORDS


class WriteSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


class DecideSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Keep the suggestion, or turn it down.
    accept: bool
    # The author's corrected words, when keeping it with changes.
    body: str | None = Field(default=None, max_length=2000)


def _summary(summary: SceneSummary) -> SceneSummaryModel:
    return SceneSummaryModel(
        id=summary.id,
        body=summary.body,
        state=summary.state.value,
        origin=summary.origin.value,
        version_id=summary.version_id,
        created_at=summary.created_at,
        model=summary.model,
        prompt_version=summary.prompt_version,
        job_id=summary.job_id,
        decided_at=summary.decided_at,
    )


def _summaries(view: SummaryView) -> SceneSummariesModel:
    return SceneSummariesModel(
        plan_id=view.plan_id,
        has_prose=view.has_prose,
        version_id=view.version_id,
        version_number=view.version_number,
        plan_card=view.plan_card,
        kept=_summary(view.kept) if view.kept is not None else None,
        kept_out_of_date=view.kept_stale,
        suggested=[_summary(item) for item in view.proposed],
    )


@router.get("/plan/{plan_id}/summary")
async def scene_summary(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(get_project_scope),
    summaries: SceneSummaryService = Depends(get_summaries),
) -> SceneSummariesModel:
    """The summary later scenes are given of this one, and any suggestion waiting."""
    view = await summaries.for_scene(scope, scope.workspace_id or "", project_id, plan_id)
    return _summaries(view)


@router.put("/plan/{plan_id}/summary")
async def write_scene_summary(
    project_id: str,
    plan_id: str,
    body: WriteSummaryRequest,
    scope: AccessScope = Depends(require_project_scope),
    summaries: SceneSummaryService = Depends(get_summaries),
) -> SceneSummariesModel:
    """Keep the author's own summary of the scene as it is written now."""
    workspace_id = scope.workspace_id or ""
    await summaries.write(scope, workspace_id, project_id, plan_id, body.body)
    return _summaries(await summaries.for_scene(scope, workspace_id, project_id, plan_id))


@router.post("/plan/{plan_id}/summary/suggest", status_code=status.HTTP_202_ACCEPTED)
async def suggest_scene_summary(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(require_project_scope),
    summaries: SceneSummaryService = Depends(get_summaries),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Ask the assistant to suggest a summary of the scene's prose; nothing is used until
    the author keeps it."""
    job = await summaries.ask(
        scope, scope.workspace_id or "", project_id, plan_id, confirmed_tokens=confirmed
    )
    return _job(job)


@router.post("/plan/{plan_id}/summary/{summary_id}")
async def decide_scene_summary(
    project_id: str,
    plan_id: str,
    summary_id: str,
    body: DecideSummaryRequest,
    scope: AccessScope = Depends(require_project_scope),
    summaries: SceneSummaryService = Depends(get_summaries),
) -> SceneSummariesModel:
    """Keep a suggested summary (as it is, or corrected), or turn it down."""
    workspace_id = scope.workspace_id or ""
    await summaries.decide(
        scope, workspace_id, project_id, plan_id, summary_id, accept=body.accept, body=body.body
    )
    return _summaries(await summaries.for_scene(scope, workspace_id, project_id, plan_id))
