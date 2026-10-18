"""What must be true before a planned scene, and what it should leave true afterwards.

A condition is about one entry and uses the same checks as a story rule, run by
the same evaluator, so a scene's conditions and the story's rules can never
disagree about what a fact pattern means. Conditions are part of the plan
(intent), kept with the scene they belong to and governed like the rest of it.

"Before" conditions say whether the story is ready for the scene. "After"
conditions say whether what the scene was meant to change has been recorded.
Neither ever blocks anything.

A condition is required (hard) or preferred (soft), and is either a check on one
entry or a sentence in the author's own words (STY-005). A sentence is never
checked; it is given to the assistant with the scene. A check is judged at the
scene's own point in story time when the scene has one, using facts with time
spans, and in plan order: an earlier scene meant to bring it about (its "after")
makes it planned, and an earlier scene meant to undo it makes it not ready.
Only a required "before" that is not true at that point is listed as something
to look at; a preferred one is a note.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.domain.rules import (
    BELIEF_KINDS,
    Check,
    RuleBody,
    Selector,
    World,
    read_rule,
    violations,
)

# Kept inside the scene's details under a reserved key, like an entry's marks.
CONDITIONS_KEY = "_conditions"
MAX_CONDITIONS = 6
MAX_WORDS_LENGTH = 300

Strength = Literal["hard", "soft"]
# met: true at that point. planned: not recorded, but an earlier scene is meant to
# make it true. not_met: not true at that point. unclear: cannot tell (the entry is
# gone, or the facts' times are too loose). unchecked: written in words.
Status = Literal["met", "planned", "not_met", "unclear", "unchecked"]


class SceneCondition(BaseModel):
    """One condition: a check on one entry, or a sentence in the author's words."""

    model_config = ConfigDict(extra="forbid")

    when: Literal["before", "after"]
    # hard: the scene needs it (required). soft: the scene would rather have it (preferred).
    strength: Strength = "hard"
    entity_id: str | None = Field(default=None, min_length=1, max_length=200)
    check: Check | None = None
    # In the author's own words, for what the story bible cannot say. Never checked.
    words: str | None = Field(default=None, max_length=MAX_WORDS_LENGTH)

    @model_validator(mode="after")
    def _one_form(self) -> SceneCondition:
        if self.words is not None:
            self.words = self.words.strip() or None
        structured = self.entity_id is not None or self.check is not None
        if self.words is not None and structured:
            raise ValueError("a condition is either about an entry or in your own words, not both")
        if self.words is None and (self.entity_id is None or self.check is None):
            raise ValueError("say which entry and what must be true, or write it in words")
        if self.check is not None and self.check.kind in BELIEF_KINDS:
            # A scene's conditions are judged against facts only; beliefs are not read.
            raise ValueError("a scene condition cannot be about what someone believes")
        return self


@dataclass(frozen=True)
class ConditionState:
    plan_id: str
    when: str
    # In ordinary words: "Mara must have this: it is located at Lantern Harbor."
    reading: str
    # Whether the story bible says it is true at that point. None when that cannot be
    # told: the entry is gone, the times are too loose, or it is written in words.
    holds: bool | None
    strength: str = "hard"
    status: str = "unclear"
    # Why, in plain words, when there is more to say than the status.
    note: str = ""
    # Its place in the scene's own list.
    index: int = 0
    # The earlier scene meant to bring it about or undo it, if any.
    planned_in: str | None = None


def scene_conditions(details: dict[str, str]) -> list[SceneCondition]:
    """A scene's conditions. Anything unreadable is treated as no conditions."""
    raw = details.get(CONDITIONS_KEY)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    found: list[SceneCondition] = []
    for item in items if isinstance(items, list) else []:
        try:
            found.append(SceneCondition.model_validate(item))
        except ValidationError:
            continue
    return found[:MAX_CONDITIONS]


def with_conditions(details: dict[str, str], conditions: list[SceneCondition]) -> dict[str, str]:
    kept = {key: value for key, value in details.items() if key != CONDITIONS_KEY}
    if conditions:
        kept[CONDITIONS_KEY] = json.dumps(
            [_stored(item) for item in conditions], separators=(",", ":")
        )
    return kept


def _stored(condition: SceneCondition) -> dict[str, object]:
    # A required condition is stored as it was before strengths existed, so saving the
    # same conditions again is still "nothing has changed".
    found = condition.model_dump(exclude_none=True)
    if found.get("strength") == "hard":
        found.pop("strength")
    return found


def plain_details(details: dict[str, str]) -> dict[str, str]:
    """A plan item's own notes, without anything kept under a reserved key."""
    return {key: value for key, value in details.items() if not key.startswith("_")}


def condition_body(condition: SceneCondition) -> RuleBody:
    """The condition as a rule about its one entry. Only for a condition about an entry."""
    if condition.entity_id is None or condition.check is None:
        raise ValueError("a condition in words has no rule")
    return RuleBody(applies_to=Selector(entity_id=condition.entity_id), checks=[condition.check])


def condition_reading(condition: SceneCondition, names: dict[str, str]) -> str:
    if condition.words is not None:
        return condition.words
    return read_rule(condition_body(condition), names)


def _verdict(plan_id: str, condition: SceneCondition, worlds: Sequence[World]) -> bool | None:
    """True or false when every world agrees; None when they differ or the entry is gone."""
    if condition.entity_id is None or not worlds:
        return None
    if any(condition.entity_id not in world.entries for world in worlds):
        return None
    results = {not violations(plan_id, condition_body(condition), world) for world in worlds}
    return results.pop() if len(results) == 1 else None


_OPPOSITE = {"has": "lacks", "lacks": "has"}


@dataclass(frozen=True)
class PlannedChange:
    """The nearest earlier scene whose "after" speaks to the same thing."""

    plan_id: str
    title: str
    # True when it is meant to make the condition true; False when meant to undo it.
    brings_about: bool


def planned_change(
    condition: SceneCondition, earlier: Sequence[tuple[str, str, Sequence[SceneCondition]]]
) -> PlannedChange | None:
    """What earlier scenes, in reading order, are meant to leave true about this.

    ``earlier`` is (plan id, title, conditions) for each scene before this one, in
    reading order. Only "has" and "lacks" checks on the same entry and the same kind
    of fact are compared, so the answer is exact rather than a guess.
    """
    if condition.check is None or condition.check.kind not in _OPPOSITE:
        return None
    for plan_id, title, conditions in reversed(earlier):
        for other in reversed(list(conditions)):
            if (
                other.when != "after"
                or other.check is None
                or other.entity_id != condition.entity_id
                or other.check.pattern != condition.check.pattern
            ):
                continue
            if other.check.kind == condition.check.kind:
                return PlannedChange(plan_id, title, True)
            if other.check.kind == _OPPOSITE[condition.check.kind]:
                return PlannedChange(plan_id, title, False)
    return None


def condition_state(
    plan_id: str,
    condition: SceneCondition,
    world: World,
    *,
    index: int = 0,
    at_time: tuple[World, World] | None = None,
    planned: PlannedChange | None = None,
    undated: World | None = None,
) -> ConditionState:
    """Whether a condition holds where its scene comes.

    ``world`` is the story as it stands. ``at_time`` is the story at the scene's own
    moment: the facts certainly true then, and those that may be. ``planned`` is what
    an earlier scene is meant to change about it (``planned_change``). ``undated`` is,
    for a scene with no story time, the story without the facts that stop being true
    at some point: when the answer turns on one of those, it cannot be told.
    """
    names = {entry.id: entry.name for entry in world.entries.values()}
    reading = condition_reading(condition, names)

    def state(holds: bool | None, status: str, note: str = "") -> ConditionState:
        return ConditionState(
            plan_id=plan_id,
            when=condition.when,
            reading=reading,
            holds=holds,
            strength=condition.strength,
            status=status,
            note=note,
            index=index,
            planned_in=planned.plan_id if planned is not None else None,
        )

    if condition.words is not None:
        return state(None, "unchecked")
    if condition.entity_id not in world.entries:
        return state(None, "unclear", "The entry it is about is no longer in the story bible.")
    if at_time is not None:
        worlds = list(at_time)
    elif undated is not None:
        worlds = [undated, world]
    else:
        worlds = [world]
    holds = _verdict(plan_id, condition, worlds)
    when_said = " when this scene happens" if at_time is not None else ""
    ended = (
        "This scene has no time in the story, and something it depends on stops being "
        "true at some point, so it may or may not hold here."
        if at_time is None and undated is not None
        else ""
    )
    if condition.when == "after":
        if holds is True:
            return state(True, "met")
        if holds is None:
            return state(
                None,
                "unclear",
                ended or "The facts it depends on are not placed clearly enough in time.",
            )
        return state(False, "not_met", f"Not recorded in the story bible{when_said} yet.")
    if planned is not None and not planned.brings_about:
        return state(
            holds,
            "not_met",
            f"“{planned.title}”, earlier in the plan, is meant to change this.",
        )
    if holds is True:
        return state(True, "met")
    if planned is not None:
        return state(
            holds,
            "planned",
            f"Not in the story bible yet; “{planned.title}”, earlier in the plan, "
            "is meant to make it true.",
        )
    if holds is None:
        return state(
            None,
            "unclear",
            ended
            or "It may or may not be true by then: the facts it depends on are not placed "
            "clearly enough in time.",
        )
    return state(False, "not_met", f"Not true in the story bible{when_said}.")
