"""The scene studio (ADR-022): a scene's prose, drafts, checks and the work behind them."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain.drafting import DraftFinding, word_count
from backend.domain.identity import AccessScope
from backend.domain.jobs import Job
from backend.domain.sources import SourceVersion
from backend.domain.studio import (
    MAX_DIRECTION,
    MAX_VARIANTS,
    MAX_VOICE,
    MAX_WORDS,
    MIN_WORDS,
    AssistantFinding,
    SceneDraft,
)
from backend.services.scene_plan_card import ScenePlanCardService
from backend.services.scene_reading import ReadingStatus, SceneReadingService
from backend.services.studio_service import StudioService, StudioState

router = APIRouter(prefix="/projects/{project_id}", tags=["studio"])


def get_studio(container: Container = Depends(get_container)) -> StudioService:
    return container.studio_service  # type: ignore[no-any-return]


def get_reading(container: Container = Depends(get_container)) -> SceneReadingService:
    return container.scene_reading  # type: ignore[no-any-return]


def get_plan_card(container: Container = Depends(get_container)) -> ScenePlanCardService:
    return container.scene_plan_card  # type: ignore[no-any-return]


async def _answer(
    state: StudioState, reading: SceneReadingService, workspace_id: str, project_id: str
) -> StudioModel:
    status = await reading.status(workspace_id, project_id, state.scene.id)
    return _model(state, status)


class CheckModel(BaseModel):
    code: str
    severity: str
    message: str
    start: int | None = None
    end: int | None = None


class AssistantNoteModel(BaseModel):
    id: str
    category: str
    message: str
    suggestion: str
    start: int | None = None
    end: int | None = None


class VersionModel(BaseModel):
    id: str
    number: int
    words: int
    origin: str
    parent_version_id: str | None = None
    draft_id: str | None = None
    manifest_id: str | None = None
    created_at: datetime


class DraftModel(BaseModel):
    id: str
    number: int
    job_id: str
    manifest_id: str | None = None
    model: str
    content: str
    words: int
    created_at: datetime
    checks: list[CheckModel]
    assistant: list[AssistantNoteModel]


class JobModel(BaseModel):
    id: str
    kind: str
    status: str
    stage: str
    progress: int
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool
    prompt_tokens: int
    completion_tokens: int
    result: dict
    created_at: datetime
    finished_at: datetime | None = None


class ReadingModel(BaseModel):
    """The last time this scene was read into the story, and whether it has changed since."""

    # None when it has never been read.
    version_id: str | None = None
    version_number: int | None = None
    read_at: datetime | None = None
    passages: int = 0
    # The proposals that came of it, waiting in the review queue or already decided.
    proposals: list[str] = []
    discarded: int = 0
    changed_since: bool


class StudioModel(BaseModel):
    scene_id: str
    title: str
    text_id: str | None = None
    version_id: str | None = None
    content: str
    words: int
    versions: list[VersionModel]
    checks: list[CheckModel]
    assistant: list[AssistantNoteModel]
    drafts: list[DraftModel]
    jobs: list[JobModel]
    reading: ReadingModel


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=500_000)
    base_version_id: str | None = None


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variants: int = Field(default=1, ge=1, le=MAX_VARIANTS)
    words: int = Field(default=800, ge=MIN_WORDS, le=MAX_WORDS)
    direction: str = Field(default="", max_length=MAX_DIRECTION)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str | None = None


class KeepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version_id: str | None = None


class EstimateModel(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    context_words: int


class VoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str = Field(default="", max_length=MAX_VOICE)


def _check(finding: DraftFinding) -> CheckModel:
    return CheckModel(
        code=finding.code,
        severity=finding.severity.value,
        message=finding.message,
        start=finding.start,
        end=finding.end,
    )


def _note(finding: AssistantFinding) -> AssistantNoteModel:
    return AssistantNoteModel(
        id=finding.id,
        category=finding.category,
        message=finding.message,
        suggestion=finding.suggestion,
        start=finding.start,
        end=finding.end,
    )


def _version(version: SourceVersion) -> VersionModel:
    return VersionModel(
        id=version.id,
        number=version.number,
        words=word_count(version.content),
        origin=version.origin.value,
        parent_version_id=version.parent_version_id,
        draft_id=version.draft_id,
        manifest_id=version.manifest_id,
        created_at=version.created_at,
    )


def _job(job: Job) -> JobModel:
    return JobModel(
        id=job.id,
        kind=job.kind,
        status=job.status.value,
        stage=job.stage,
        progress=job.progress,
        error_code=job.error_code,
        error_message=job.error_message,
        cancel_requested=job.cancel_requested,
        prompt_tokens=job.prompt_tokens,
        completion_tokens=job.completion_tokens,
        result=job.result,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


def _draft(draft: SceneDraft, state: StudioState) -> DraftModel:
    return DraftModel(
        id=draft.id,
        number=draft.number,
        job_id=draft.job_id,
        manifest_id=draft.manifest_id,
        model=draft.model,
        content=draft.content,
        words=word_count(draft.content),
        created_at=draft.created_at,
        checks=[_check(f) for f in state.draft_checks.get(draft.id, [])],
        assistant=[_note(f) for f in state.draft_assistant.get(draft.id, [])],
    )


def _reading(status: ReadingStatus) -> ReadingModel:
    last = status.last
    return ReadingModel(
        version_id=last.version_id if last else None,
        version_number=status.version_number,
        read_at=last.created_at if last else None,
        passages=last.passages if last else 0,
        proposals=list(last.proposals) if last else [],
        discarded=last.discarded if last else 0,
        changed_since=status.changed_since,
    )


def _model(state: StudioState, reading: ReadingStatus) -> StudioModel:
    content = state.current.content if state.current is not None else ""
    return StudioModel(
        scene_id=state.scene.id,
        title=state.scene.title,
        text_id=state.text.id if state.text is not None else None,
        version_id=state.current.id if state.current is not None else None,
        content=content,
        words=word_count(content),
        versions=[_version(v) for v in reversed(state.versions)],
        checks=[_check(f) for f in state.checks],
        assistant=[_note(f) for f in state.assistant],
        drafts=[_draft(d, state) for d in state.drafts],
        jobs=[_job(j) for j in state.jobs],
        reading=_reading(reading),
    )


@router.get("/plan/{plan_id}/studio")
async def studio(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: StudioService = Depends(get_studio),
    reading: SceneReadingService = Depends(get_reading),
) -> StudioModel:
    state = await service.state(scope, scope.workspace_id or "", project_id, plan_id)
    return await _answer(state, reading, scope.workspace_id or "", project_id)


@router.put("/plan/{plan_id}/studio/text")
async def save_text(
    project_id: str,
    plan_id: str,
    body: SaveRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: StudioService = Depends(get_studio),
    reading: SceneReadingService = Depends(get_reading),
) -> StudioModel:
    state = await service.save(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        content=body.content,
        base_version_id=body.base_version_id,
    )
    return await _answer(state, reading, scope.workspace_id or "", project_id)


@router.post("/plan/{plan_id}/studio/estimate")
async def estimate(
    project_id: str,
    plan_id: str,
    body: DraftRequest,
    scope: AccessScope = Depends(get_project_scope),
    service: StudioService = Depends(get_studio),
) -> EstimateModel:
    found = await service.estimate(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        variants=body.variants,
        words=body.words,
    )
    return EstimateModel(
        prompt_tokens=found.prompt_tokens,
        completion_tokens=found.completion_tokens,
        total_tokens=found.total,
        context_words=found.context_words,
    )


@router.post("/plan/{plan_id}/studio/drafts", status_code=status.HTTP_202_ACCEPTED)
async def start_draft(
    project_id: str,
    plan_id: str,
    body: DraftRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: StudioService = Depends(get_studio),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> JobModel:
    job = await service.start_draft(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        variants=body.variants,
        words=body.words,
        direction=body.direction,
        idempotency_key=idempotency_key or "",
    )
    return _job(job)


@router.post("/plan/{plan_id}/studio/reviews", status_code=status.HTTP_202_ACCEPTED)
async def start_review(
    project_id: str,
    plan_id: str,
    body: ReviewRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: StudioService = Depends(get_studio),
) -> JobModel:
    job = await service.start_review(
        scope, scope.workspace_id or "", project_id, plan_id, draft_id=body.draft_id
    )
    return _job(job)


@router.post("/plan/{plan_id}/studio/readings", status_code=status.HTTP_202_ACCEPTED)
async def start_reading(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(require_project_scope),
    reading: SceneReadingService = Depends(get_reading),
) -> JobModel:
    """Read the scene into proposed changes to the story, for review (Phase 6)."""
    job = await reading.start(scope, scope.workspace_id or "", project_id, plan_id)
    return _job(job)


@router.post("/plan/{plan_id}/studio/plan-card", status_code=status.HTTP_202_ACCEPTED)
async def start_plan_card(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(require_project_scope),
    plan_card: ScenePlanCardService = Depends(get_plan_card),
) -> JobModel:
    """Fill in the scene's plan card from its prose, as a plan change for review (Phase 6)."""
    job = await plan_card.start(scope, scope.workspace_id or "", project_id, plan_id)
    return _job(job)


@router.post("/drafts/{draft_id}/keep")
async def keep_draft(
    project_id: str,
    draft_id: str,
    body: KeepRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: StudioService = Depends(get_studio),
    reading: SceneReadingService = Depends(get_reading),
) -> StudioModel:
    state = await service.keep(
        scope, scope.workspace_id or "", project_id, draft_id, base_version_id=body.base_version_id
    )
    return await _answer(state, reading, scope.workspace_id or "", project_id)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_draft(
    project_id: str,
    draft_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: StudioService = Depends(get_studio),
) -> None:
    await service.discard(scope, scope.workspace_id or "", project_id, draft_id)


@router.get("/jobs/{job_id}")
async def job(
    project_id: str,
    job_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: StudioService = Depends(get_studio),
) -> JobModel:
    return _job(await service.job(scope, scope.workspace_id or "", project_id, job_id))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    project_id: str,
    job_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: StudioService = Depends(get_studio),
) -> JobModel:
    return _job(await service.cancel(scope, scope.workspace_id or "", project_id, job_id))


@router.get("/voice")
async def voice(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: StudioService = Depends(get_studio),
) -> VoiceModel:
    return VoiceModel(notes=await service.voice(scope, scope.workspace_id or "", project_id))


@router.put("/voice")
async def set_voice(
    project_id: str,
    body: VoiceModel,
    scope: AccessScope = Depends(require_project_scope),
    service: StudioService = Depends(get_studio),
) -> VoiceModel:
    notes = await service.set_voice(scope, scope.workspace_id or "", project_id, body.notes)
    return VoiceModel(notes=notes)
