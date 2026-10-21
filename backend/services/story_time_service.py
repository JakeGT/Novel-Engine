"""The story's timeline: events in story-time order, and what was true at a moment.

Reads only; every change to a time goes through the ordinary reviewed change path
(``amend_event``, ``amend_fact``). See ADR-020 and ``backend/domain/worldtime.py``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.domain.canon.assertions import Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.tenancy import Permission
from backend.domain.worldtime import (
    Between,
    Interval,
    Resolved,
    TimeProblem,
    Truth,
    describe,
    resolve,
    resolve_all,
    span_of,
    true_at,
)
from backend.ports import CanonRepositoryPort

MAX_RECORDS = 10_000
DEFAULT_UNIT = "year"
MAX_UNIT = 30


@dataclass(frozen=True)
class Story:
    """Everything current in one story, read once for a timeline or knowledge query."""

    entities: dict[str, CanonEntity]
    facts: list[Fact]
    events: list[Event]


async def read_story(reader: CanonRepositoryPort, workspace_id: str, project_id: str) -> Story:
    entities = await reader.list_entities(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    facts = await reader.list_facts(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    events = await reader.list_events(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    return Story(
        entities={entity.id: entity for entity in entities},
        facts=list(facts),
        events=[event for event in events if event.retired_revision is None],
    )


def event_times(events: list[Event]) -> dict[str, Resolved]:
    return resolve_all({event.id: event.when for event in events})


def _bound(value: float) -> float | None:
    return None if math.isinf(value) else value


@dataclass(frozen=True)
class PlacedEvent:
    event: Event
    words: str
    earliest: float | None
    latest: float | None
    problem: str | None = None


@dataclass(frozen=True)
class SpannedFact:
    fact: Fact
    starts: str | None
    ends: str | None


@dataclass(frozen=True)
class TimeIssue:
    code: str
    message: str
    record_ids: tuple[str, ...]


@dataclass
class Timeline:
    unit: str
    placed: list[PlacedEvent] = field(default_factory=list)
    unplaced: list[PlacedEvent] = field(default_factory=list)
    spans: list[SpannedFact] = field(default_factory=list)
    issues: list[TimeIssue] = field(default_factory=list)


@dataclass(frozen=True)
class TrueThen:
    fact: Fact
    truth: Truth


@dataclass(frozen=True)
class Moment:
    unit: str
    at: float
    facts: list[TrueThen]
    happened: list[PlacedEvent]


_PROBLEM_WORDS = {
    TimeProblem.CYCLE: "is placed relative to events that are placed relative to it",
    TimeProblem.MISSING_ANCHOR: "is placed relative to an event that is no longer in the story",
}


class StoryTimeService:
    def __init__(
        self,
        *,
        reader: CanonRepositoryPort,
        clocks: Any,
        tenancy: Any = None,
        local_mode: bool = False,
    ) -> None:
        self._reader = reader
        self._clocks = clocks
        self._tenancy = tenancy
        self._local_mode = local_mode
        # The story's ready views (QRY-001), set by the container: the timeline answers
        # from its view once that is verified and current, else it is worked out here.
        self.views: Any = None

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    async def unit(self, workspace_id: str, project_id: str) -> str:
        stored = await self._clocks.get_unit(workspace_id=workspace_id, project_id=project_id)
        return stored or DEFAULT_UNIT

    async def set_unit(
        self, scope: AccessScope, workspace_id: str, project_id: str, unit: str
    ) -> str:
        """Name the unit the story counts time in: "year", "day", "season"."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        cleaned = " ".join(unit.split()).lower()
        if not cleaned or len(cleaned) > MAX_UNIT or not cleaned.replace(" ", "").isalpha():
            raise ValidationError("Name the unit in a word or two, such as year, day or season.")
        # Kept in the singular; descriptions add an "s" where needed.
        if cleaned.endswith("s") and len(cleaned) > 3:
            cleaned = cleaned[:-1]
        await self._clocks.set_unit(workspace_id=workspace_id, project_id=project_id, unit=cleaned)
        return cleaned

    async def timeline(self, scope: AccessScope, workspace_id: str, project_id: str) -> Timeline:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        unit = await self.unit(workspace_id, project_id)
        if self.views is not None:
            ready = await self.views.timeline(workspace_id, project_id, unit)
            if ready is not None:
                return ready  # type: ignore[no-any-return]
        story = await read_story(self._reader, workspace_id, project_id)
        return build_timeline(story, unit)

    async def moment(
        self, scope: AccessScope, workspace_id: str, project_id: str, at: float
    ) -> Moment:
        """What was true, and what had happened, at one moment of story time."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        story = await read_story(self._reader, workspace_id, project_id)
        unit = await self.unit(workspace_id, project_id)
        return moment_in(story, unit, at)

    async def continuity(self, scope: AccessScope, workspace_id: str, project_id: str) -> list[Any]:
        """Continuity across story time and place (CON-003, CON-004): warnings and notes."""
        from backend.services.continuity_service import story_continuity

        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await story_continuity(self._reader, self._clocks, workspace_id, project_id)

    async def how_long(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> tuple[Any, int]:
        """The author's table of travel times and lengths, and its version."""
        from backend.services.continuity_service import how_long_of

        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await how_long_of(self._clocks, workspace_id, project_id)

    async def set_how_long(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        table: Any,
        expected_version: int,
    ) -> int:
        """Replace the table; a setting of the story, like its unit, never a change to canon."""
        from backend.services.continuity_service import save_how_long

        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        return await save_how_long(
            self._reader, self._clocks, workspace_id, project_id, table, expected_version
        )


def _place(event: Event, resolved: Resolved, unit: str, names: dict[str, str]) -> PlacedEvent:
    interval = resolved.interval
    return PlacedEvent(
        event=event,
        words=describe(event.when, unit=unit, event_name=names.get),
        earliest=_bound(interval.lo) if interval else None,
        latest=_bound(interval.hi) if interval else None,
        problem=resolved.problem.value if resolved.problem else None,
    )


def build_timeline(story: Story, unit: str) -> Timeline:
    names = {event.id: event.name for event in story.events}
    times = event_times(story.events)
    timeline = Timeline(unit=unit)
    for event in story.events:
        resolved = times[event.id]
        placed = _place(event, resolved, unit, names)
        if resolved.interval is None:
            timeline.unplaced.append(placed)
        else:
            timeline.placed.append(placed)
        if resolved.problem is not None:
            timeline.issues.append(
                TimeIssue(
                    resolved.problem.value,
                    f"{event.name} {_PROBLEM_WORDS[resolved.problem]}.",
                    (event.id,),
                )
            )
    timeline.placed.sort(
        key=lambda item: (
            times[item.event.id].interval.lo,  # type: ignore[union-attr]
            times[item.event.id].interval.hi,  # type: ignore[union-attr]
            item.event.name.lower(),
        )
    )
    timeline.unplaced.sort(key=lambda item: item.event.name.lower())
    for fact in story.facts:
        if fact.starts is None and fact.ends is None:
            continue
        timeline.spans.append(
            SpannedFact(
                fact=fact,
                starts=describe(fact.starts, unit=unit, event_name=names.get)
                if fact.starts
                else None,
                ends=describe(fact.ends, unit=unit, event_name=names.get) if fact.ends else None,
            )
        )
    return timeline


def anchor_lookup(times: dict[str, Resolved]) -> Callable[[str], Any]:
    """How a relative time finds its anchor: as the interval that event resolved to."""

    def when_of(event_id: str) -> Any:
        resolved = times.get(event_id)
        if resolved is None:
            return False
        if resolved.interval is None:
            return None
        interval = resolved.interval
        return Between(
            earliest=None if math.isinf(interval.lo) else interval.lo,
            latest=None if math.isinf(interval.hi) else interval.hi,
        )

    return when_of


def time_of(when: Any, times: dict[str, Resolved]) -> Interval | None:
    """Resolve a time that may be relative to the story's events."""
    return resolve(when, anchor_lookup(times)).interval


def fact_span(fact: Fact, times: dict[str, Resolved]) -> Any:
    when_of = anchor_lookup(times)
    return span_of(
        resolve(fact.starts, when_of) if fact.starts is not None else None,
        resolve(fact.ends, when_of) if fact.ends is not None else None,
    )


def moment_in(story: Story, unit: str, at: float) -> Moment:
    names = {event.id: event.name for event in story.events}
    times = event_times(story.events)
    moment = Interval(at, at)
    facts = []
    for fact in story.facts:
        truth = true_at(fact_span(fact, times), moment)
        if truth is not Truth.FALSE:
            facts.append(TrueThen(fact, truth))
    happened = [
        _place(event, times[event.id], unit, names)
        for event in story.events
        if (interval := times[event.id].interval) is not None and interval.lo <= at
    ]
    happened.sort(key=lambda item: item.earliest if item.earliest is not None else -math.inf)
    return Moment(unit=unit, at=at, facts=facts, happened=happened)


__all__ = [
    "Moment",
    "PlacedEvent",
    "SpannedFact",
    "Story",
    "StoryTimeService",
    "TimeIssue",
    "Timeline",
    "TrueThen",
    "build_timeline",
    "event_times",
    "anchor_lookup",
    "fact_span",
    "moment_in",
    "read_story",
    "time_of",
]
