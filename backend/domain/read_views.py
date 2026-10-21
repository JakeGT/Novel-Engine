"""Views of the story kept ready to read, built from its records (QRY-001, ADR-020).

Six views, each a list of small rows holding record ids, predicates and story times,
never names or the author's words:

* ``location``     who or what is where: ``LOCATED_AT`` facts with their span, and the
                   people in timed events and scenes that have one place;
* ``possession``   who holds what (``OWNS``, ``HOLDS`` and the other holding words);
* ``relationship`` facts between two characters, or of a relationship kind;
* ``timeline``     events in story-time order with their interval, facts with a span,
                   and the plan's scenes in reading order with their time;
* ``disclosure``   what each scene shows the reader, and the irony and secrets at it;
* ``dependency``   which record leans on which (a fact on its entries and the events its
                   times hang on, a scene on the entries it names, and so on).

The builders are pure and deterministic: the same records give the same rows in the
same order, whether they come from today's tables or from replaying the record history
(``story_at``), which is what replay verification compares. Times are those the
timeline uses (``story_time_service.fact_span`` and ``event_times``): exact as stated,
so the whole-unit reading of a coarse clock that the continuity checks apply is theirs,
not the views'.

``BUILDER_VERSION`` is raised whenever any builder's output changes; a view built by
another version is rebuilt and is not trusted again until it is verified.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import CLAIM_KIND, Event
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.knowledge import reader_journey, reading_order, scene_reveals, scene_time
from backend.domain.worldtime import (
    Between,
    Interval,
    Resolved,
    Span,
    Truth,
    anchors,
    resolve,
    resolve_all,
    span_of,
    true_at,
)

BUILDER_VERSION = 1
VIEWS: tuple[str, ...] = (
    "location",
    "possession",
    "relationship",
    "timeline",
    "disclosure",
    "dependency",
)
LOCATED = "LOCATED_AT"
POSSESSION_KINDS = frozenset({"OWNS", "HOLDS", "CARRIES", "POSSESSES", "WIELDS"})
RELATIONSHIP_KINDS = frozenset({"RELATED_TO", "KNOWS", "MEMBER_OF"})
_BODIES = frozenset({"Character", "Object"})
# How far "what leans on this" is followed, and how many records it lists at most.
MAX_DEPTH = 3
MAX_DEPENDENTS = 500

Row = dict[str, Any]


@dataclass(frozen=True)
class Snapshot:
    """The current records of one line of the story, from wherever they were read."""

    entities: Mapping[str, CanonEntity]
    facts: Sequence[Fact]
    beliefs: Sequence[Belief]
    events: Sequence[Event]
    plan_items: Sequence[PlanItem]


def current(snapshot: Snapshot) -> Snapshot:
    """Only what is in the story now: both sources are filtered the same way."""
    return Snapshot(
        entities={key: value for key, value in snapshot.entities.items() if value.is_active},
        facts=[fact for fact in snapshot.facts if fact.closed_revision is None],
        beliefs=[belief for belief in snapshot.beliefs if belief.closed_revision is None],
        events=[event for event in snapshot.events if event.retired_revision is None],
        plan_items=[item for item in snapshot.plan_items if item.retired_revision is None],
    )


# ------------------------------------------------------------------ encoding


def pair(interval: Interval | None) -> list[float | None] | None:
    """An interval as two numbers; an unbounded end is null. None: cannot be placed."""
    if interval is None:
        return None
    return [
        None if math.isinf(interval.lo) else float(interval.lo),
        None if math.isinf(interval.hi) else float(interval.hi),
    ]


def interval_of(value: Sequence[float | None] | None) -> Interval | None:
    if value is None:
        return None
    lo, hi = value
    return Interval(-math.inf if lo is None else float(lo), math.inf if hi is None else float(hi))


def span_json(span: Span) -> Row:
    return {"start": pair(span.start), "end": pair(span.end), "stated": span.stated}


def span_from(row: Mapping[str, Any]) -> Span:
    return Span(
        start=interval_of(row.get("start")),
        end=interval_of(row.get("end")),
        stated=bool(row.get("stated")),
    )


def _normal(value: Any) -> Any:
    """Numbers as floats, so rows read back from storage compare equal to fresh ones."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _normal(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normal(item) for item in value]
    return value


def canonical(row: Mapping[str, Any]) -> str:
    return json.dumps(_normal(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(rows: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical(row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _sorted(rows: Iterable[Row]) -> list[Row]:
    unique = {canonical(row): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def differences(stored: Iterable[Mapping[str, Any]], rebuilt: Iterable[Mapping[str, Any]]) -> int:
    """How many rows one side has and the other does not."""
    left = {canonical(row) for row in stored}
    right = {canonical(row) for row in rebuilt}
    return len(left ^ right)


# ------------------------------------------------------------------ times


@dataclass
class _Times:
    events: dict[str, Resolved]

    def when_of(self, event_id: str) -> Any:
        resolved = self.events.get(event_id)
        if resolved is None:
            return False
        if resolved.interval is None:
            return None
        interval = resolved.interval
        return Between(
            earliest=None if math.isinf(interval.lo) else interval.lo,
            latest=None if math.isinf(interval.hi) else interval.hi,
        )

    def of(self, when: Any) -> Interval | None:
        if when is None:
            return None
        return resolve(when, self.when_of).interval

    def span(self, fact: Fact) -> Span:
        return span_of(
            resolve(fact.starts, self.when_of) if fact.starts is not None else None,
            resolve(fact.ends, self.when_of) if fact.ends is not None else None,
        )


def _times(snapshot: Snapshot) -> _Times:
    return _Times(resolve_all({event.id: event.when for event in snapshot.events}))


def _object_entity(fact: Fact | Belief) -> str | None:
    target = getattr(fact.proposition.object, "entity_id", None)
    return target if isinstance(target, str) else None


# ------------------------------------------------------------------ builders


@dataclass
class _Context:
    snapshot: Snapshot
    times: _Times
    scenes: list[PlanItem] = field(default_factory=list)
    scene_at: dict[str, Interval | None] = field(default_factory=dict)


def _context(snapshot: Snapshot) -> _Context:
    times = _times(snapshot)
    scenes = reading_order(snapshot.plan_items)
    return _Context(
        snapshot=snapshot,
        times=times,
        scenes=scenes,
        scene_at={scene.id: times.of(scene_time(scene.details)) for scene in scenes},
    )


def _location(c: _Context) -> list[Row]:
    entities = c.snapshot.entities
    rows: list[Row] = []
    for fact in c.snapshot.facts:
        place = _object_entity(fact)
        if fact.proposition.predicate != LOCATED or place not in entities:
            continue
        rows.append(
            {
                "entity_id": fact.proposition.subject_id,
                "place_id": place,
                "source": "fact",
                "record_id": fact.id,
                "span": span_json(c.times.span(fact)),
            }
        )
    for event in c.snapshot.events:
        if event.kind == CLAIM_KIND:
            continue
        at = c.times.events[event.id].interval
        places = [pid for pid in event.location_ids if pid in entities] or [
            item.entity_id
            for item in event.participants
            if (entity := entities.get(item.entity_id)) is not None and entity.type == "Location"
        ]
        places = list(dict.fromkeys(places))
        if at is None or len(places) != 1:
            continue
        for item in event.participants:
            entity = entities.get(item.entity_id)
            if entity is None or entity.type not in _BODIES:
                continue
            rows.append(
                {
                    "entity_id": item.entity_id,
                    "place_id": places[0],
                    "source": "event",
                    "record_id": event.id,
                    "at": pair(at),
                }
            )
    for scene in c.scenes:
        at = c.scene_at.get(scene.id)
        place = next(iter(scene.refs_as("place")), None)
        if at is None or place is None:
            continue
        where = entities.get(place)
        if where is None or where.type != "Location":
            continue
        people = dict.fromkeys([*scene.refs_as("point_of_view"), *scene.refs_as("participant")])
        for entity_id in people:
            entity = entities.get(entity_id)
            if entity is None or entity.type not in _BODIES:
                continue
            rows.append(
                {
                    "entity_id": entity_id,
                    "place_id": place,
                    "source": "scene",
                    "record_id": scene.id,
                    "at": pair(at),
                }
            )
    return _sorted(rows)


def _possession(c: _Context) -> list[Row]:
    entities = c.snapshot.entities
    rows: list[Row] = []
    for fact in c.snapshot.facts:
        thing = _object_entity(fact)
        if fact.proposition.predicate not in POSSESSION_KINDS:
            continue
        if thing not in entities or fact.proposition.subject_id not in entities:
            continue
        rows.append(
            {
                "item_id": thing,
                "holder_id": fact.proposition.subject_id,
                "predicate": fact.proposition.predicate,
                "fact_id": fact.id,
                "span": span_json(c.times.span(fact)),
            }
        )
    return _sorted(rows)


def _relationship(c: _Context) -> list[Row]:
    entities = c.snapshot.entities
    rows: list[Row] = []
    for fact in c.snapshot.facts:
        other = _object_entity(fact)
        subject = entities.get(fact.proposition.subject_id)
        target = entities.get(other or "")
        if subject is None or target is None:
            continue
        between_characters = subject.type == "Character" and target.type == "Character"
        if not between_characters and fact.proposition.predicate not in RELATIONSHIP_KINDS:
            continue
        rows.append(
            {
                "a": subject.id,
                "b": target.id,
                "predicate": fact.proposition.predicate,
                "fact_id": fact.id,
                "span": span_json(c.times.span(fact)),
            }
        )
    return _sorted(rows)


def _timeline(c: _Context) -> list[Row]:
    rows: list[Row] = []
    placed: list[tuple[float, float, str, str]] = []
    for event in c.snapshot.events:
        resolved = c.times.events[event.id]
        rows.append(
            {
                "kind": "event",
                "id": event.id,
                "at": pair(resolved.interval),
                "problem": resolved.problem.value if resolved.problem else None,
            }
        )
        if resolved.interval is not None:
            placed.append(
                (resolved.interval.lo, resolved.interval.hi, event.name.lower(), event.id)
            )
    order = {item[3]: number for number, item in enumerate(sorted(placed), start=1)}
    for row in rows:
        row["order"] = order.get(row["id"])
    for fact in c.snapshot.facts:
        if fact.starts is None and fact.ends is None:
            continue
        rows.append({"kind": "span", "id": fact.id, "span": span_json(c.times.span(fact))})
    for number, scene in enumerate(c.scenes, start=1):
        rows.append(
            {
                "kind": "scene",
                "id": scene.id,
                "number": number,
                "at": pair(c.scene_at.get(scene.id)),
            }
        )
    return _sorted(rows)


def _disclosure(c: _Context) -> list[Row]:
    steps = reader_journey(c.scenes, c.snapshot.facts, c.snapshot.beliefs, dict(c.scene_at))
    rows: list[Row] = []
    for step in steps:
        rows.append(
            {
                "scene_id": step.scene.id,
                "number": step.number,
                "point_of_view": step.point_of_view,
                "shown": sorted(reveal.fact_id for reveal in step.shown if reveal.how == "shown"),
                "hinted": sorted(reveal.fact_id for reveal in step.shown if reveal.how == "hinted"),
                "irony": list(step.irony),
                "withheld": list(step.withheld),
                "repeated": sorted(step.repeated),
                "flashback": step.flashback,
            }
        )
    return _sorted(rows)


def _dependency(c: _Context) -> list[Row]:
    snapshot = c.snapshot
    kinds: dict[str, str] = {}
    for kind, ids in (
        ("entity", snapshot.entities.keys()),
        ("fact", (fact.id for fact in snapshot.facts)),
        ("belief", (belief.id for belief in snapshot.beliefs)),
        ("event", (event.id for event in snapshot.events)),
        ("plan_item", (item.id for item in snapshot.plan_items)),
    ):
        for record_id in ids:
            kinds.setdefault(record_id, kind)
    rows: list[Row] = []

    def edge(source: str, target: str | None, why: str) -> None:
        if not target or target not in kinds or target == source.split(":", 1)[1]:
            return
        rows.append({"from": source, "to": f"{kinds[target]}:{target}", "why": why})

    def times(source: str, *whens: Any) -> None:
        for when in whens:
            if when is not None:
                for anchor in anchors(when):
                    edge(source, anchor, "time")

    for fact in snapshot.facts:
        key = f"fact:{fact.id}"
        edge(key, fact.proposition.subject_id, "subject")
        edge(key, _object_entity(fact), "object")
        times(key, fact.starts, fact.ends)
    for belief in snapshot.beliefs:
        key = f"belief:{belief.id}"
        edge(key, belief.holder_entity_id, "holder")
        edge(key, belief.proposition.subject_id, "subject")
        edge(key, _object_entity(belief), "object")
    for event in snapshot.events:
        key = f"event:{event.id}"
        for participant in event.participants:
            edge(key, participant.entity_id, "takes part")
        for place in event.location_ids:
            edge(key, place, "place")
        for cause in event.cause_ids:
            edge(key, cause, "cause")
        for outcome in event.outcome_ids:
            edge(key, outcome, "outcome")
        for premise in event.premises:
            edge(key, premise, "worked out from")
        edge(key, event.scene_id, "scene")
        times(key, event.when)
    for item in snapshot.plan_items:
        key = f"plan_item:{item.id}"
        for ref in item.refs:
            edge(key, ref.entity_id, ref.role)
        edge(key, item.parent_id, "part of")
        for thread in item.sets_up:
            edge(key, thread, "sets up")
        for thread in item.pays_off:
            edge(key, thread, "pays off")
        if item.kind is PlanKind.SCENE:
            for reveal in scene_reveals(item.details):
                edge(key, reveal.fact_id, "shows" if reveal.how == "shown" else "hints at")
            times(key, scene_time(item.details))
    return _sorted(rows)


_BUILDERS = {
    "location": _location,
    "possession": _possession,
    "relationship": _relationship,
    "timeline": _timeline,
    "disclosure": _disclosure,
    "dependency": _dependency,
}


def build_views(snapshot: Snapshot, only: Iterable[str] | None = None) -> dict[str, list[Row]]:
    """Every view (or the ones named) of the story's current records."""
    wanted = list(only) if only is not None else list(VIEWS)
    unknown = [name for name in wanted if name not in _BUILDERS]
    if unknown:
        raise ValueError(f"unknown view: {unknown[0]}")
    context = _context(current(snapshot))
    return {name: _BUILDERS[name](context) for name in wanted}


# ------------------------------------------------------------------ questions


def _moment(at: float | None) -> Interval | None:
    return None if at is None else Interval(float(at), float(at))


def _truth_of(row: Mapping[str, Any], moment: Interval | None) -> Truth:
    if moment is None:
        return Truth.TRUE
    if "span" in row:
        return true_at(span_from(row["span"]), moment)
    at = interval_of(row.get("at"))
    if at is None:
        return Truth.FALSE
    if at.lo <= moment.lo and moment.hi <= at.hi and at.is_point:
        return Truth.TRUE
    if at.hi < moment.lo or at.lo > moment.hi:
        return Truth.FALSE
    return Truth.TRUE if at.is_point else Truth.MAYBE


def at_moment(
    rows: Iterable[Mapping[str, Any]], at: float | None, **match: str
) -> list[tuple[Mapping[str, Any], Truth]]:
    """Rows matching every ``key=value`` given, with whether each holds at ``at``
    (every row, as true, when no moment is asked about). Rows that do not hold are left out."""
    moment = _moment(at)
    found: list[tuple[Mapping[str, Any], Truth]] = []
    for row in rows:
        if any(row.get(key) != value for key, value in match.items()):
            continue
        truth = _truth_of(row, moment)
        if truth is not Truth.FALSE:
            found.append((row, truth))
    return found


@dataclass(frozen=True)
class Dependent:
    record: str
    why: str
    depth: int
    # The record it leans on, on the way back to the one asked about.
    via: str


def dependents(
    rows: Iterable[Mapping[str, Any]],
    record: str,
    *,
    depth: int = MAX_DEPTH,
    limit: int = MAX_DEPENDENTS,
) -> tuple[list[Dependent], bool]:
    """What leans on ``record`` (``kind:id``), nearest first. And whether the list was cut."""
    leaning: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        leaning.setdefault(str(row["to"]), []).append((str(row["from"]), str(row["why"])))
    for found in leaning.values():
        found.sort()
    seen = {record}
    frontier = [record]
    out: list[Dependent] = []
    for level in range(1, max(1, depth) + 1):
        next_frontier: list[str] = []
        for target in frontier:
            for source, why in leaning.get(target, []):
                if source in seen:
                    continue
                seen.add(source)
                if len(out) >= limit:
                    return out, True
                out.append(Dependent(source, why, level, target))
                next_frontier.append(source)
        frontier = next_frontier
        if not frontier:
            break
    return out, False


__all__ = [
    "BUILDER_VERSION",
    "Dependent",
    "MAX_DEPENDENTS",
    "MAX_DEPTH",
    "Snapshot",
    "VIEWS",
    "at_moment",
    "build_views",
    "canonical",
    "checksum",
    "current",
    "dependents",
    "differences",
    "interval_of",
    "pair",
    "span_from",
    "span_json",
]
