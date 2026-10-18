"""Deep audits of a part of the story (CON-011): estimate, start, list and read results.

An audit is a background job like any other: followed with ``GET /jobs/{id}`` and stopped
with ``POST /jobs/{id}/cancel``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.assistant_settings import confirmed_tokens
from backend.api.v1.studio import (
    AssistantNoteModel,
    CheckModel,
    JobModel,
    PassEstimateModel,
    _check,
    _job,
    _note,
)
from backend.container import Container
from backend.domain.check_passes import PASS_DESCRIPTIONS, PASS_LABELS, CheckPass
from backend.domain.identity import AccessScope
from backend.services.story_audit import MAX_AUDIT_SCENES, StoryAudit

router = APIRouter(prefix="/projects/{project_id}", tags=["audits"])


def get_audits(container: Container = Depends(get_container)) -> StoryAudit:
    return container.studio_service.audits  # type: ignore[no-any-return]


class AuditSceneEstimateModel(BaseModel):
    plan_id: str
    title: str
    written: bool
    words: int
    total_tokens: int


class AuditEstimateModel(BaseModel):
    plan_id: str
    title: str
    scenes: list[AuditSceneEstimateModel]
    written: int
    max_scenes: int
    passes: list[PassEstimateModel]
    total_tokens: int


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A part of the plan (story, arc, beat or scene); empty for the whole plan.
    plan_id: str = Field(default="", max_length=200)
    # What the assistant checks in each scene; every pass when left out, and none (only
    # the checks that need no model) when empty.
    passes: list[CheckPass] | None = Field(default=None, max_length=len(CheckPass))


MAX_QUOTED = 600


class AuditNoteModel(AssistantNoteModel):
    # The words the note is about, from the version the audit read.
    quoted: str = ""


class AuditCheckModel(CheckModel):
    quoted: str = ""


def _quoted(content: str, start: int | None, end: int | None) -> str:
    if start is None or end is None or not 0 <= start < end <= len(content):
        return ""
    return content[start:end][:MAX_QUOTED]


class AuditSceneModel(BaseModel):
    plan_id: str
    title: str
    in_plan: bool
    version_id: str
    version_number: int | None = None
    current: bool
    # checked, partial (the audit stopped part of the way through it), gone (removed from
    # the plan meanwhile), not_checked (the audit stopped first) or waiting (still running).
    status: str
    # For a partly checked scene: the passes that finished in it.
    passes_done: list[CheckPass] = Field(default_factory=list)
    checks: list[AuditCheckModel]
    story_checks: list[str]
    notes: list[AuditNoteModel]


class AuditUnwrittenModel(BaseModel):
    plan_id: str
    title: str


class AuditModel(BaseModel):
    job: JobModel
    plan_id: str
    title: str
    passes: list[CheckPass]
    scenes: list[AuditSceneModel]
    unwritten: list[AuditUnwrittenModel]


def _title(item: object | None, plan_id: str = "") -> str:
    if item is None:
        return "A part of the plan no longer there" if plan_id else "The whole story"
    return str(getattr(item, "title", "")) or "Untitled"


@router.get("/audits/estimate")
async def audit_estimate(
    project_id: str,
    plan_id: str = Query(default="", max_length=200),
    passes: list[CheckPass] | None = Query(default=None, max_length=len(CheckPass)),
    scope: AccessScope = Depends(get_project_scope),
    audits: StoryAudit = Depends(get_audits),
) -> AuditEstimateModel:
    """What auditing this part of the story would read and use, before anything is sent.
    Every pass when none are named. Calls no model."""
    found = await audits.estimate(scope, scope.workspace_id or "", project_id, plan_id, passes)
    totals = found.pass_totals()
    return AuditEstimateModel(
        plan_id=plan_id,
        title=_title(found.item),
        scenes=[
            AuditSceneEstimateModel(
                plan_id=entry.scene.id,
                title=entry.scene.title,
                written=entry.written,
                words=len(entry.version.content.split()) if entry.version else 0,
                total_tokens=entry.tokens,
            )
            for entry in found.scenes
        ],
        written=len(found.written),
        max_scenes=MAX_AUDIT_SCENES,
        passes=[
            PassEstimateModel(
                check_pass=check,
                label=PASS_LABELS[check],
                description=PASS_DESCRIPTIONS[check],
                prompt_tokens=sum(
                    item.prompt_tokens
                    for entry in found.scenes
                    for item in entry.estimates
                    if item.check is check
                ),
                completion_tokens=sum(
                    item.completion_tokens
                    for entry in found.scenes
                    for item in entry.estimates
                    if item.check is check
                ),
                total_tokens=totals.get(check, 0),
            )
            for check in found.passes
        ],
        total_tokens=found.total,
    )


@router.post("/audits", status_code=status.HTTP_202_ACCEPTED)
async def start_audit(
    project_id: str,
    body: AuditRequest,
    scope: AccessScope = Depends(require_project_scope),
    audits: StoryAudit = Depends(get_audits),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Start auditing a part of the story. Above the size the author or workspace asked to
    confirm, refused with ``409 needs_confirmation`` until confirmed."""
    job = await audits.start(
        scope,
        scope.workspace_id or "",
        project_id,
        body.plan_id,
        passes=body.passes,
        confirmed_tokens=confirmed,
        idempotency_key=idempotency_key or "",
    )
    return _job(job)


class AuditListModel(BaseModel):
    items: list[JobModel]


@router.get("/audits")
async def list_audits(
    project_id: str,
    plan_id: str = Query(default="", max_length=200),
    scope: AccessScope = Depends(get_project_scope),
    audits: StoryAudit = Depends(get_audits),
) -> AuditListModel:
    """The latest audits of this part of the story, newest first."""
    jobs = await audits.recent(scope, scope.workspace_id or "", project_id, plan_id)
    return AuditListModel(items=[_job(job) for job in jobs])


@router.get("/audits/{job_id}")
async def audit(
    project_id: str,
    job_id: str,
    scope: AccessScope = Depends(get_project_scope),
    audits: StoryAudit = Depends(get_audits),
) -> AuditModel:
    """An audit's results, scene by scene, with the checks that need no model."""
    found = await audits.report(scope, scope.workspace_id or "", project_id, job_id)
    return AuditModel(
        job=_job(found.job),
        plan_id=str(found.job.payload.get("plan_id") or ""),
        title=_title(found.item, str(found.job.payload.get("plan_id") or "")),
        passes=list(found.passes),
        scenes=[
            AuditSceneModel(
                plan_id=scene.plan_id,
                title=scene.title,
                in_plan=scene.in_plan,
                version_id=scene.version_id,
                version_number=scene.version_number,
                current=scene.current,
                status=scene.status,
                passes_done=list(scene.passes_done),
                checks=[
                    AuditCheckModel(
                        **_check(item).model_dump(),
                        quoted=_quoted(scene.content, item.start, item.end),
                    )
                    for item in scene.checks
                ],
                story_checks=scene.story_checks,
                notes=[
                    AuditNoteModel(
                        **_note(item).model_dump(),
                        quoted=_quoted(scene.content, item.start, item.end),
                    )
                    for item in scene.notes
                ],
            )
            for scene in found.scenes
        ],
        unwritten=[
            AuditUnwrittenModel(plan_id=item.id, title=item.title) for item in found.unwritten
        ],
    )


__all__ = ["router"]
