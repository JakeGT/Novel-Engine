"""Deep audits of a part of the story (CON-011): estimate, start, list and read results.

An audit is a background job like any other: followed with ``GET /jobs/{id}`` and stopped
with ``POST /jobs/{id}/cancel``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
from backend.api.v1.voice import (
    UsualModel,
    VoiceNoteModel,
    VoiceStatsModel,
    note_model,
    stats_model,
    usual_model,
)
from backend.container import Container
from backend.domain.audit_plans import (
    MAX_EVERY_DAYS,
    MAX_SCHEDULE_TOKENS,
    MIN_EVERY_DAYS,
    OUTCOME_WORDS,
    AuditSchedule,
    CorrectionPlan,
)
from backend.domain.check_passes import PASS_DESCRIPTIONS, PASS_LABELS, CheckPass
from backend.domain.identity import AccessScope
from backend.services.audit_schedules import AuditScheduleService
from backend.services.correction_plans import CorrectionPlanService
from backend.services.story_audit import (
    MAX_AUDIT_SCENES,
    WHAT_IF_CHANGES,
    AuditEstimate,
    StoryAudit,
)

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
    if plan_id == WHAT_IF_CHANGES:
        return "The scenes written differently in this what-if"
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
    return _estimate_model(found, plan_id)


def _estimate_model(found: AuditEstimate, plan_id: str) -> AuditEstimateModel:
    totals = found.pass_totals()
    return AuditEstimateModel(
        plan_id=plan_id,
        title=_title(found.item, plan_id),
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


# ------------------------------------------------------- drift across the scenes read


class VoiceRowModel(BaseModel):
    plan_id: str
    title: str
    version_number: int
    stats: VoiceStatsModel
    notes: list[VoiceNoteModel]


class PairModel(BaseModel):
    names: list[str]
    # The scenes of the audit that have them both.
    together: list[str]
    # What the story bible records between them now.
    recorded: list[str]
    notes: list[str]


class AuditDriftModel(BaseModel):
    """How voice and relationships move across the scenes an audit read. Calls no model."""

    voice: list[VoiceRowModel]
    usual: UsualModel
    trends: list[str]
    relationships: list[PairModel]


@router.get("/audits/{job_id}/drift")
async def audit_drift(
    project_id: str,
    job_id: str,
    scope: AccessScope = Depends(get_project_scope),
    audits: StoryAudit = Depends(get_audits),
) -> AuditDriftModel:
    """Voice against the author's usual, and how each pair of characters drifts, across the
    exact versions the audit read, in reading order. Worked out now, for free."""
    found = await audits.drift(scope, scope.workspace_id or "", project_id, job_id)
    return AuditDriftModel(
        voice=[
            VoiceRowModel(
                plan_id=reading.plan_id,
                title=reading.title,
                version_number=reading.version_number,
                stats=stats_model(reading.stats),
                notes=[note_model(note) for note in notes],
            )
            for reading, notes in found.voice.rows
        ],
        usual=usual_model(found.voice.usual),
        trends=[trend.message for trend in found.voice.trends],
        relationships=[
            PairModel(
                names=list(row.names),
                together=row.together,
                recorded=row.recorded,
                notes=row.notes,
            )
            for row in found.relationships
        ],
    )


# ------------------------------------------------------------------ correction plans


def get_corrections(container: Container = Depends(get_container)) -> CorrectionPlanService:
    return container.correction_plans  # type: ignore[no-any-return]


class CorrectionItemModel(BaseModel):
    plan_id: str
    title: str
    change: str
    why: str
    # What each finding it answers said, when the plan was made.
    cited: list[str]
    # waiting, kept or skipped.
    decision: Literal["waiting", "kept", "skipped"]


class CorrectionPlanModel(BaseModel):
    id: str
    summary: str
    items: list[CorrectionItemModel]
    # Steps the assistant suggested that were thrown away (no finding, or a scene the
    # audit did not read).
    dropped: int
    created_at: datetime | None = None


class CorrectionsModel(BaseModel):
    plans: list[CorrectionPlanModel]
    # Planning asked for and not yet finished (or just finished), newest first.
    jobs: list[JobModel]


class CorrectionEstimateModel(BaseModel):
    total_tokens: int
    findings: int
    scenes: int


def _plan_model(plan: CorrectionPlan) -> CorrectionPlanModel:
    return CorrectionPlanModel(
        id=plan.id,
        summary=plan.summary,
        items=[
            CorrectionItemModel(
                plan_id=item.plan_id,
                title=item.title,
                change=item.change,
                why=item.why,
                cited=list(item.cited),
                decision=item.decision,
            )
            for item in plan.items
        ],
        dropped=plan.dropped,
        created_at=plan.created_at,
    )


@router.get("/audits/{job_id}/corrections")
async def corrections(
    project_id: str,
    job_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: CorrectionPlanService = Depends(get_corrections),
) -> CorrectionsModel:
    """The correction plans made from this audit, newest first."""
    plans, jobs = await service.plans(scope, scope.workspace_id or "", project_id, job_id)
    return CorrectionsModel(
        plans=[_plan_model(plan) for plan in plans], jobs=[_job(j) for j in jobs]
    )


@router.get("/audits/{job_id}/corrections/estimate")
async def corrections_estimate(
    project_id: str,
    job_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: CorrectionPlanService = Depends(get_corrections),
) -> CorrectionEstimateModel:
    """What planning the corrections would send, before anything is. Calls no model."""
    found = await service.estimate(scope, scope.workspace_id or "", project_id, job_id)
    return CorrectionEstimateModel(**found)


@router.post("/audits/{job_id}/corrections", status_code=status.HTTP_202_ACCEPTED)
async def plan_corrections(
    project_id: str,
    job_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: CorrectionPlanService = Depends(get_corrections),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Ask the assistant which scenes to change and why, from this audit's findings. Above
    the size the author asked to confirm, refused with ``409 needs_confirmation``."""
    job = await service.start(
        scope, scope.workspace_id or "", project_id, job_id, confirmed_tokens=confirmed
    )
    return _job(job)


class DecideStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["waiting", "kept", "skipped"]


@router.put("/corrections/{plan_id}/items/{index}")
async def decide_step(
    project_id: str,
    plan_id: str,
    index: int,
    body: DecideStepRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: CorrectionPlanService = Depends(get_corrections),
) -> CorrectionPlanModel:
    """Keep or skip one step of a correction plan. Nothing in the story changes."""
    plan = await service.decide(
        scope, scope.workspace_id or "", project_id, plan_id, index, body.decision
    )
    return _plan_model(plan)


# --------------------------------------------------------------------- schedules


def get_schedules(container: Container = Depends(get_container)) -> AuditScheduleService:
    return container.audit_schedules  # type: ignore[no-any-return]


class ScheduleModel(BaseModel):
    id: str
    plan_id: str
    passes: list[CheckPass]
    every_days: int
    max_tokens: int
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None = None
    last_job_id: str | None = None
    # What the last run came to, as a sentence.
    last_outcome: str | None = None


class ScheduleListModel(BaseModel):
    items: list[ScheduleModel]
    max_every_days: int = MAX_EVERY_DAYS
    max_tokens: int = MAX_SCHEDULE_TOKENS


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(default="", max_length=200)
    passes: list[CheckPass] = Field(default_factory=list, max_length=len(CheckPass))
    every_days: int = Field(ge=MIN_EVERY_DAYS, le=MAX_EVERY_DAYS)
    max_tokens: int = Field(ge=0, le=MAX_SCHEDULE_TOKENS)


def _schedule_model(item: AuditSchedule) -> ScheduleModel:
    return ScheduleModel(
        id=item.id,
        plan_id=item.plan_id,
        passes=[CheckPass(value) for value in item.passes if value in CheckPass._value2member_map_],
        every_days=item.every_days,
        max_tokens=item.max_tokens,
        enabled=item.enabled,
        next_run_at=item.next_run_at,
        last_run_at=item.last_run_at,
        last_job_id=item.last_job_id,
        last_outcome=OUTCOME_WORDS.get(item.last_outcome or "") if item.last_outcome else None,
    )


@router.get("/audit-schedules")
async def list_schedules(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    schedules: AuditScheduleService = Depends(get_schedules),
) -> ScheduleListModel:
    """The audits set to run on a schedule in this line of the story."""
    items = await schedules.list(scope, scope.workspace_id or "", project_id)
    return ScheduleListModel(items=[_schedule_model(item) for item in items])


@router.put("/audit-schedules")
async def save_schedule(
    project_id: str,
    body: ScheduleRequest,
    scope: AccessScope = Depends(require_project_scope),
    schedules: AuditScheduleService = Depends(get_schedules),
    confirmed: int | None = Depends(confirmed_tokens),
) -> ScheduleModel:
    """Run an audit of a part of the story every few days, using at most ``max_tokens`` each
    time. A most above the size the author asked to confirm is confirmed first (``409``)."""
    saved = await schedules.save(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id=body.plan_id,
        passes=body.passes,
        every_days=body.every_days,
        max_tokens=body.max_tokens,
        confirmed_tokens=confirmed,
    )
    return _schedule_model(saved)


@router.delete("/audit-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_schedule(
    project_id: str,
    schedule_id: str,
    scope: AccessScope = Depends(require_project_scope),
    schedules: AuditScheduleService = Depends(get_schedules),
) -> None:
    """Stop a scheduled audit. Audits it already ran are kept."""
    await schedules.remove(scope, scope.workspace_id or "", project_id, schedule_id)


# ------------------------------------------------------------ comparing a what-if


@router.get("/what-ifs/{what_if_id}/audit/changed/estimate")
async def changed_audit_estimate(
    project_id: str,
    what_if_id: str,
    passes: list[CheckPass] | None = Query(default=None, max_length=len(CheckPass)),
    scope: AccessScope = Depends(get_project_scope),
    audits: StoryAudit = Depends(get_audits),
) -> AuditEstimateModel:
    """What asking the assistant to check only the scenes this what-if has written
    differently from the main story would read and use, before anything is sent. Every
    pass when none are named. Calls no model."""
    found, _ = await audits.estimate_changed(
        scope, scope.workspace_id or "", project_id, what_if_id, passes
    )
    return _estimate_model(found, WHAT_IF_CHANGES)


class ChangedAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passes: list[CheckPass] = Field(min_length=1, max_length=len(CheckPass))


@router.post("/what-ifs/{what_if_id}/audit/changed", status_code=status.HTTP_202_ACCEPTED)
async def start_changed_audit(
    project_id: str,
    what_if_id: str,
    body: ChangedAuditRequest,
    scope: AccessScope = Depends(require_project_scope),
    audits: StoryAudit = Depends(get_audits),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Ask the assistant to check only the scenes this what-if has written differently, in
    the what-if. Above the size the author asked to confirm, refused with ``409
    needs_confirmation`` until confirmed. The results are an audit like any other."""
    job = await audits.start_changed(
        scope,
        scope.workspace_id or "",
        project_id,
        what_if_id,
        passes=body.passes,
        confirmed_tokens=confirmed,
        idempotency_key=idempotency_key or "",
    )
    return _job(job)


# --------------------------------------------------------------------- notices


class NoticeModel(BaseModel):
    """An in-app notice for the author: a scheduled audit finished (CON-011)."""

    id: str
    kind: Literal["audit_finished"]
    job_id: str
    # succeeded, failed (kept what it finished) or cancelled (kept what it finished).
    status: Literal["succeeded", "failed", "cancelled"]
    # What it says, in plain words, with what it found and used.
    sentence: str
    # What the audit covered, as named now.
    part: str
    plan_id: str
    # The what-if it ran in, by name; null for the main story.
    what_if: str | None = None
    branch_id: str | None = None
    scenes: int
    notes: int
    checks: int
    total_tokens: int
    cost_usd: float | None = None
    created_at: datetime | None = None
    seen: bool


class NoticeListModel(BaseModel):
    items: list[NoticeModel]


def get_notices(container: Container = Depends(get_container)) -> Any:
    return container.audit_notices


@router.get("/notices")
async def list_notices(
    project_id: str,
    job_id: str | None = Query(default=None, max_length=200),
    unseen: bool = Query(default=False),
    scope: AccessScope = Depends(get_project_scope),
    notices: Any = Depends(get_notices),
) -> NoticeListModel:
    """Your own notices in this story, newest first: one for each audit you scheduled that
    has finished. ``unseen`` keeps only those not yet marked read; ``job_id`` one audit's."""
    if notices is None:
        return NoticeListModel(items=[])
    found = await notices.list(
        scope, scope.workspace_id or "", project_id, job_id=job_id, unseen_only=unseen
    )
    return NoticeListModel(
        items=[
            NoticeModel(
                id=item.notice.id,
                kind=item.notice.kind,
                job_id=item.notice.job_id,
                status=item.notice.status,
                sentence=item.sentence,
                part=item.part,
                plan_id=item.notice.plan_id,
                what_if=item.what_if,
                branch_id=item.notice.branch_id,
                scenes=item.notice.scenes,
                notes=item.notice.notes,
                checks=item.notice.checks,
                total_tokens=item.notice.tokens,
                cost_usd=item.notice.cost_usd,
                created_at=item.notice.created_at,
                seen=item.notice.seen_at is not None,
            )
            for item in found
        ]
    )


@router.post("/notices/{notice_id}/seen", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notice_seen(
    project_id: str,
    notice_id: str,
    scope: AccessScope = Depends(get_project_scope),
    notices: Any = Depends(get_notices),
) -> None:
    """Mark one of your notices read. It stays listed, as read."""
    from backend.domain.errors import NotFoundError

    if notices is None or not await notices.mark_seen(
        scope, scope.workspace_id or "", project_id, notice_id
    ):
        raise NotFoundError("That notice was not found.")


class WhatIfDifferenceModel(BaseModel):
    # entry, fact, event or plan
    kind: str
    # added (in the what-if only), removed (in the main story only) or changed
    change: Literal["added", "removed", "changed"]
    title: str
    detail: str
    plan_id: str | None = None


class WhatIfSceneModel(BaseModel):
    plan_id: str
    title: str
    main_words: int
    what_if_words: int
    voice: list[str]


class WhatIfAuditModel(BaseModel):
    """What differs between a what-if and the main story, worked out now. Calls no model."""

    what_if_id: str
    what_if_name: str
    same: bool
    bible: list[WhatIfDifferenceModel]
    plan: list[WhatIfDifferenceModel]
    scenes: list[WhatIfSceneModel]
    # Continuity findings and plan problems found only in the what-if, or only in the main story.
    new_findings: list[str]
    gone_findings: list[str]
    left_out: list[str]


@router.get("/what-ifs/{what_if_id}/audit")
async def audit_what_if(
    project_id: str,
    what_if_id: str,
    scope: AccessScope = Depends(get_project_scope),
    container: Container = Depends(get_container),
) -> WhatIfAuditModel:
    """An audit of the differences between a what-if and the main story."""
    found = await container.what_if_audit.compare(
        scope, scope.workspace_id or "", project_id, what_if_id
    )
    return WhatIfAuditModel(
        what_if_id=found.what_if_id,
        what_if_name=found.what_if_name,
        same=found.same,
        bible=[WhatIfDifferenceModel(**item.__dict__) for item in found.bible],
        plan=[WhatIfDifferenceModel(**item.__dict__) for item in found.plan],
        scenes=[WhatIfSceneModel(**item.__dict__) for item in found.scenes],
        new_findings=found.new_findings,
        gone_findings=found.gone_findings,
        left_out=found.left_out,
    )


__all__ = ["router"]
