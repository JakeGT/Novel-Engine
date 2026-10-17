"""Story time: when things happen inside the fiction (ADR-020).

A story counts time on its own number line, in one unit it names ("year" by default).
A time is unknown, at a point, between two points, or relative to an event ("three
years before the war"). The author's own words are kept as a label beside the value,
and nothing is ever converted to a calendar date.

Resolving a time gives the narrowest interval it is certainly inside, or ``None`` when
nothing is known. Two resolved times compare as before, after, or overlapping; an
unknown one compares as unknown. Narrative order (the order scenes are read in) and
system revisions are different clocks and never convert to or from this one.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

MAX_LABEL = 120
# Far enough for any story; keeps arithmetic finite and JSON-safe.
LIMIT = 1e12


class _When(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The author's own words for this time, kept as they wrote them.
    label: str | None = Field(default=None, max_length=MAX_LABEL)


class Unknown(_When):
    kind: Literal["unknown"] = "unknown"


class At(_When):
    kind: Literal["at"] = "at"
    at: float = Field(ge=-LIMIT, le=LIMIT)


class Between(_When):
    kind: Literal["between"] = "between"
    # Either end may be open: "some time before year 12" has only ``latest``.
    earliest: float | None = Field(default=None, ge=-LIMIT, le=LIMIT)
    latest: float | None = Field(default=None, ge=-LIMIT, le=LIMIT)

    @model_validator(mode="after")
    def _ordered(self) -> Between:
        if self.earliest is None and self.latest is None:
            raise ValueError("a span of time needs at least one end")
        if self.earliest is not None and self.latest is not None and self.earliest > self.latest:
            raise ValueError("the earliest time is after the latest")
        return self


class Direction(StrEnum):
    BEFORE = "before"
    AFTER = "after"


class Relative(_When):
    """So long before or after an event: "three years before the war"."""

    kind: Literal["relative"] = "relative"
    anchor_event_id: str = Field(min_length=1, max_length=200)
    offset: float = Field(default=0, ge=0, le=LIMIT)
    direction: Direction = Direction.AFTER
    # "About three years": how far either way the offset may be out.
    give_or_take: float = Field(default=0, ge=0, le=LIMIT)


WorldTime = Annotated[Unknown | At | Between | Relative, Field(discriminator="kind")]
WORLD_TIME: TypeAdapter[Unknown | At | Between | Relative] = TypeAdapter(WorldTime)


def parse_world_time(value: object) -> Unknown | At | Between | Relative | None:
    """A stored time, or ``None`` when there is none or it no longer parses."""
    if value is None:
        return None
    try:
        return WORLD_TIME.validate_python(value)
    except ValueError:
        return None


def is_unknown(value: object) -> bool:
    return value is None or isinstance(value, Unknown)


@dataclass(frozen=True)
class Interval:
    """A closed span the time is certainly inside. Either end may be unbounded."""

    lo: float
    hi: float

    @property
    def is_point(self) -> bool:
        return self.lo == self.hi

    def shift(self, by: float, widen: float = 0) -> Interval:
        return Interval(self.lo + by - widen, self.hi + by + widen)


class Order(StrEnum):
    BEFORE = "before"
    AFTER = "after"
    OVERLAPS = "overlaps"
    UNKNOWN = "unknown"


def compare(a: Interval | None, b: Interval | None) -> Order:
    """Whether ``a`` is certainly before or after ``b``, or the two may meet."""
    if a is None or b is None:
        return Order.UNKNOWN
    if a.hi < b.lo:
        return Order.BEFORE
    if a.lo > b.hi:
        return Order.AFTER
    return Order.OVERLAPS


class TimeProblem(StrEnum):
    CYCLE = "cycle"
    MISSING_ANCHOR = "missing_anchor"


@dataclass(frozen=True)
class Resolved:
    interval: Interval | None
    problem: TimeProblem | None = None


def resolve(
    when: Unknown | At | Between | Relative | None,
    when_of: Callable[[str], Unknown | At | Between | Relative | None | Literal[False]],
    *,
    _seen: frozenset[str] = frozenset(),
) -> Resolved:
    """Resolve a time to the interval it is certainly inside.

    ``when_of(event_id)`` returns that event's time, ``None`` when it has none, or
    ``False`` when there is no such event. A chain of relative times is followed;
    a chain that comes back on itself is reported, not looped.
    """
    if when is None or isinstance(when, Unknown):
        return Resolved(None)
    if isinstance(when, At):
        return Resolved(Interval(when.at, when.at))
    if isinstance(when, Between):
        return Resolved(
            Interval(
                when.earliest if when.earliest is not None else -math.inf,
                when.latest if when.latest is not None else math.inf,
            )
        )
    anchor_id = when.anchor_event_id
    if anchor_id in _seen:
        return Resolved(None, TimeProblem.CYCLE)
    anchor = when_of(anchor_id)
    if anchor is False:
        return Resolved(None, TimeProblem.MISSING_ANCHOR)
    inner = resolve(anchor, when_of, _seen=_seen | {anchor_id})
    if inner.interval is None:
        return Resolved(None, inner.problem)
    sign = -1 if when.direction is Direction.BEFORE else 1
    return Resolved(inner.interval.shift(sign * when.offset, when.give_or_take))


def resolve_all(
    times: Mapping[str, Unknown | At | Between | Relative | None],
) -> dict[str, Resolved]:
    """Resolve every event's time against the others."""

    def when_of(event_id: str) -> Unknown | At | Between | Relative | None | Literal[False]:
        return times[event_id] if event_id in times else False

    return {
        event_id: resolve(when, when_of, _seen=frozenset({event_id}))
        for event_id, when in times.items()
    }


# ------------------------------------------------------------------ fact spans


@dataclass(frozen=True)
class Span:
    """When a fact is true. A missing end means it reaches the start or end of the story."""

    start: Interval | None
    end: Interval | None
    # Whether the span says anything at all. A fact with no span is true throughout.
    stated: bool


def span_of(start: Resolved | None, end: Resolved | None) -> Span:
    return Span(
        start=start.interval if start else None,
        end=end.interval if end else None,
        stated=bool((start and start.interval) or (end and end.interval)),
    )


class Truth(StrEnum):
    TRUE = "true"
    FALSE = "false"
    MAYBE = "maybe"


def true_at(span: Span, moment: Interval) -> Truth:
    """Whether a fact with this span holds at a moment (itself possibly fuzzy).

    A span is ``[start, end)``: true from its start, no longer true at its end.
    """
    if not span.stated:
        return Truth.TRUE
    certainly_started = span.start is None or span.start.hi <= moment.lo
    possibly_started = span.start is None or span.start.lo <= moment.hi
    certainly_not_ended = span.end is None or span.end.lo > moment.hi
    possibly_not_ended = span.end is None or span.end.hi > moment.lo
    if certainly_started and certainly_not_ended:
        return Truth.TRUE
    if not possibly_started or not possibly_not_ended:
        return Truth.FALSE
    return Truth.MAYBE


def spans_overlap(a: Span, b: Span) -> bool:
    """Whether two spans may share a moment. Unstated spans cover the whole story."""
    if not a.stated or not b.stated:
        return True
    a_start = a.start.lo if a.start else -math.inf
    b_start = b.start.lo if b.start else -math.inf
    a_end = a.end.hi if a.end else math.inf
    b_end = b.end.hi if b.end else math.inf
    # Half-open: one ending exactly where the other starts does not overlap.
    return a_start < b_end and b_start < a_end


# ------------------------------------------------------------------ words


def number_words(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def unit_words(unit: str, amount: float) -> str:
    return unit if amount == 1 else f"{unit}s"


def describe(
    when: Unknown | At | Between | Relative | None,
    *,
    unit: str = "year",
    event_name: Callable[[str], str | None] = lambda _id: None,
) -> str:
    """Plain words for a time, preferring the author's own."""
    if when is None or isinstance(when, Unknown):
        return when.label if when is not None and when.label else "not placed in time"
    if when.label:
        return when.label
    if isinstance(when, At):
        return f"{unit} {number_words(when.at)}"
    if isinstance(when, Between):
        if when.earliest is not None and when.latest is not None:
            return f"between {unit} {number_words(when.earliest)} and {number_words(when.latest)}"
        if when.earliest is not None:
            return f"after {unit} {number_words(when.earliest)}"
        return f"before {unit} {number_words(when.latest or 0)}"
    anchor = event_name(when.anchor_event_id) or "an event no longer in the story"
    if when.offset == 0:
        return f"at the time of {anchor}"
    about = "about " if when.give_or_take else ""
    return (
        f"{about}{number_words(when.offset)} {unit_words(unit, when.offset)} "
        f"{when.direction.value} {anchor}"
    )


def anchors(when: Unknown | At | Between | Relative | None) -> Iterable[str]:
    if isinstance(when, Relative):
        yield when.anchor_event_id


__all__ = [
    "At",
    "Between",
    "Direction",
    "Interval",
    "Order",
    "Relative",
    "Resolved",
    "Span",
    "TimeProblem",
    "Truth",
    "Unknown",
    "WORLD_TIME",
    "WorldTime",
    "anchors",
    "compare",
    "describe",
    "is_unknown",
    "parse_world_time",
    "resolve",
    "resolve_all",
    "span_of",
    "spans_overlap",
    "true_at",
]
