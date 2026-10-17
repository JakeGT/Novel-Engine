"""Story time as the assistant writes it, and its strict conversion (ADR-020).

The shape a model fills in is flat and forgiving; ``to_world_time`` turns it into
a checked ``WorldTime`` or says why it cannot. Nothing is guessed: a time the
model could not state stays unknown.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from backend.domain.worldtime import WORLD_TIME, At, Between, Direction, Relative, Unknown

MAX_WORDS = 120


class StoryTime(BaseModel):
    kind: str = Field(description="unknown, at, between, or relative")
    at: float | None = Field(default=None, description="For at: the point in story time")
    earliest: float | None = Field(default=None, description="For between: the earliest point")
    latest: float | None = Field(default=None, description="For between: the latest point")
    anchor: str | None = Field(
        default=None, description="For relative: the id of an existing event, or a new event's ref"
    )
    offset: float | None = Field(default=None, description="For relative: how long before or after")
    direction: str | None = Field(default=None, description="For relative: before or after")
    about: bool = Field(default=False, description="True when the author said roughly or about")
    words: str | None = Field(
        default=None, description="The author's own words for this time, if they gave any"
    )


def to_world_time(
    value: StoryTime, *, anchors: dict[str, str] | None = None
) -> tuple[Unknown | At | Between | Relative | None, str]:
    """A checked time, or ``None`` and the reason it could not be read."""
    label = (value.words or "").strip()[:MAX_WORDS] or None
    kind = value.kind.strip().lower()
    try:
        if kind == "unknown":
            return Unknown(label=label), ""
        if kind == "at":
            if value.at is None:
                return None, "a time at a point needs the point"
            return At(at=value.at, label=label), ""
        if kind == "between":
            return Between(earliest=value.earliest, latest=value.latest, label=label), ""
        if kind == "relative":
            anchor = (value.anchor or "").strip()
            anchor = (anchors or {}).get(anchor, anchor)
            if not anchor:
                return None, "a time relative to an event needs the event"
            direction = (value.direction or "after").strip().lower()
            if direction not in {"before", "after"}:
                return None, f"{direction!r} is not before or after"
            offset = abs(value.offset or 0)
            return (
                Relative(
                    anchor_event_id=anchor,
                    offset=offset,
                    direction=Direction(direction),
                    # "About three years": allow a third either way, at least one unit.
                    give_or_take=max(1.0, offset / 3) if value.about and offset else 0,
                    label=label,
                ),
                "",
            )
    except (ValidationError, ValueError) as error:
        return None, str(error).splitlines()[0][:200]
    return None, f"{value.kind!r} is not a kind of time"


def round_trip(value: object) -> object:
    """A time exactly as it would be stored, for comparisons."""
    return WORLD_TIME.validate_python(value)


__all__ = ["StoryTime", "to_world_time"]
