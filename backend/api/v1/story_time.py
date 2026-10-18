"""The story's timeline: events in story-time order, and what was true at a moment."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.canon_assertions import EventModel, FactModel, _event_model, _fact_model
from backend.container import Container
from backend.domain.continuity import (
    ALWAYS_SAID,
    COVERAGE,
    MAX_LASTING_ROWS,
    MAX_TRAVEL_ROWS,
    ContinuityFinding,
    HowLong,
    Lasting,
    Record,
    TravelTime,
    table_problem,
)
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.services.story_time_service import PlacedEvent, StoryTimeService

router = APIRouter(prefix="/projects/{project_id}", tags=["story-time"])


def get_story_time_service(container: Container = Depends(get_container)) -> StoryTimeService:
    return container.story_time_service  # type: ignore[no-any-return]


class PlacedEventModel(BaseModel):
    event: EventModel
    # The time in words, the author's own where they gave them.
    when: str
    # The span the event is certainly inside; ``None`` is open-ended or unknown.
    earliest: float | None = None
    latest: float | None = None
    problem: str | None = None


class SpannedFactModel(BaseModel):
    fact: FactModel
    starts: str | None = None
    ends: str | None = None


class TimeIssueModel(BaseModel):
    code: str
    message: str
    record_ids: list[str]


class TimelineModel(BaseModel):
    unit: str
    placed: list[PlacedEventModel]
    unplaced: list[PlacedEventModel]
    spans: list[SpannedFactModel]
    issues: list[TimeIssueModel]


class TrueThenModel(BaseModel):
    fact: FactModel
    # "true", or "maybe" where the fact's start or end is uncertain around this moment.
    truth: str


class MomentModel(BaseModel):
    unit: str
    at: float
    facts: list[TrueThenModel]
    happened: list[PlacedEventModel]


class UnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str = Field(min_length=1, max_length=40)


class UnitModel(BaseModel):
    unit: str


class RecordModel(BaseModel):
    """A record a finding comes from, so it can be opened."""

    # entry, fact, event or scene (a part of the plan).
    kind: str
    id: str
    label: str
    # For a fact: the entry it is about, whose page shows it.
    entry_id: str | None = None


class ContinuityFindingModel(BaseModel):
    # Which check found it: simultaneity, travel, age, duration, place, possession,
    # life, state or change.
    check: str
    code: str
    # warning: these cannot all be true. note: worth a look. Never blocking.
    severity: str
    message: str
    records: list[RecordModel]
    # The scenes (plan ids) it is about.
    scene_ids: list[str]


class CoverageModel(BaseModel):
    check: str
    title: str
    # Exactly what the check looks at, in plain words.
    covers: str


class ContinuityModel(BaseModel):
    findings: list[ContinuityFindingModel]
    # More found than are listed: warnings and notes are each capped.
    not_shown: int = 0
    coverage: list[CoverageModel]
    # What holds for every check.
    always: str


class TravelTimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_id: str = Field(min_length=1, max_length=200)
    to_id: str = Field(min_length=1, max_length=200)
    # In the story's unit; 0 says one place is inside the other.
    amount: float = Field(ge=0, le=1e12)


class LastingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_kind: Literal["event", "fact"]
    subject_id: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0, le=1e12)
    give_or_take: float = Field(default=0, ge=0, le=1e12)


class HowLongModel(BaseModel):
    travel: list[TravelTimeModel]
    lasting: list[LastingModel]
    # Goes up with every save; send it back to save again.
    version: int


class HowLongRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    travel: list[TravelTimeModel] = Field(default_factory=list, max_length=MAX_TRAVEL_ROWS)
    lasting: list[LastingModel] = Field(default_factory=list, max_length=MAX_LASTING_ROWS)
    expected_version: int = Field(ge=0)


def record_model(record: Record) -> RecordModel:
    return RecordModel(kind=record.kind, id=record.id, label=record.label, entry_id=record.entry_id)


def finding_model(finding: ContinuityFinding) -> ContinuityFindingModel:
    return ContinuityFindingModel(
        check=finding.check,
        code=finding.code,
        severity=finding.severity,
        message=finding.message,
        records=[record_model(record) for record in finding.records],
        scene_ids=list(finding.scene_ids),
    )


def _how_long_model(table: HowLong, version: int) -> HowLongModel:
    return HowLongModel(
        travel=[TravelTimeModel(**row.model_dump()) for row in table.travel],
        lasting=[LastingModel(**row.model_dump()) for row in table.lasting],
        version=version,
    )


def _placed(item: PlacedEvent) -> PlacedEventModel:
    return PlacedEventModel(
        event=_event_model(item.event),
        when=item.words,
        earliest=item.earliest,
        latest=item.latest,
        problem=item.problem,
    )


@router.get("/timeline")
async def get_timeline(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: StoryTimeService = Depends(get_story_time_service),
) -> TimelineModel:
    timeline = await service.timeline(scope, scope.workspace_id or "", project_id)
    return TimelineModel(
        unit=timeline.unit,
        placed=[_placed(item) for item in timeline.placed],
        unplaced=[_placed(item) for item in timeline.unplaced],
        spans=[
            SpannedFactModel(fact=_fact_model(item.fact), starts=item.starts, ends=item.ends)
            for item in timeline.spans
        ],
        issues=[
            TimeIssueModel(
                code=issue.code, message=issue.message, record_ids=list(issue.record_ids)
            )
            for issue in timeline.issues
        ],
    )


@router.get("/timeline/moment")
async def get_moment(
    project_id: str,
    at: float = Query(ge=-1e12, le=1e12),
    scope: AccessScope = Depends(get_project_scope),
    service: StoryTimeService = Depends(get_story_time_service),
) -> MomentModel:
    moment = await service.moment(scope, scope.workspace_id or "", project_id, at)
    return MomentModel(
        unit=moment.unit,
        at=moment.at,
        facts=[
            TrueThenModel(fact=_fact_model(item.fact), truth=item.truth.value)
            for item in moment.facts
        ],
        happened=[_placed(item) for item in moment.happened],
    )


@router.put("/timeline/unit")
async def set_unit(
    project_id: str,
    body: UnitRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: StoryTimeService = Depends(get_story_time_service),
) -> UnitModel:
    unit = await service.set_unit(scope, scope.workspace_id or "", project_id, body.unit)
    return UnitModel(unit=unit)


@router.get("/timeline/checks")
async def get_continuity(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: StoryTimeService = Depends(get_story_time_service),
) -> ContinuityModel:
    """Continuity across story time and place: warnings and notes, never blocking."""
    findings = await service.continuity(scope, scope.workspace_id or "", project_id)
    return ContinuityModel(
        findings=[finding_model(item) for item in findings],
        not_shown=getattr(findings, "not_shown", 0),
        coverage=[
            CoverageModel(check=item.check, title=item.title, covers=item.covers)
            for item in COVERAGE
        ],
        always=ALWAYS_SAID,
    )


@router.get("/timeline/how-long")
async def get_how_long(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: StoryTimeService = Depends(get_story_time_service),
) -> HowLongModel:
    table, version = await service.how_long(scope, scope.workspace_id or "", project_id)
    return _how_long_model(table, version)


@router.put("/timeline/how-long")
async def set_how_long(
    project_id: str,
    body: HowLongRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: StoryTimeService = Depends(get_story_time_service),
) -> HowLongModel:
    """Replace the author's travel times and lengths. Shared by every line of the story."""
    try:
        table = HowLong(
            travel=[TravelTime(**row.model_dump()) for row in body.travel],
            lasting=[Lasting(**row.model_dump()) for row in body.lasting],
        )
    except PydanticValidationError as error:
        problem = error.errors()[0].get("msg", "") if error.errors() else ""
        raise ValidationError(table_problem(str(problem))) from None
    version = await service.set_how_long(
        scope, scope.workspace_id or "", project_id, table, body.expected_version
    )
    return _how_long_model(table, version)
