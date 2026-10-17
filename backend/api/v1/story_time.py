"""The story's timeline: events in story-time order, and what was true at a moment."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.canon_assertions import EventModel, FactModel, _event_model, _fact_model
from backend.container import Container
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
