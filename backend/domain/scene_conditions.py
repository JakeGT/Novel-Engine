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

A condition can also be about what a character knows (STY-005, batch 10): "Mara knows
that Ivo is at the tower", "Mara has seen it", "Mara has heard it". It is judged against
the beliefs recorded for her and how each was learned (``backend.domain.acquisition``):
knowing needs a belief that agrees, or a fact everyone knows; seeing needs one she
learned by seeing it happen; hearing, one she was told, overheard or heard said. A
belief she only comes to hold after the scene does not count, and for a "before" one
learned in the scene itself does not either. Nothing is guessed: a belief whose learning
cannot be placed counts as known all along, as everywhere else in the knowledge checks.

In plan order, besides "has" and "lacks" on the same kind of fact, an earlier scene
meant to leave someone somewhere else undoes "is at" (a person is in one place at a
time), an earlier "knows" (or "has seen" / "has heard" for the same way) brings a
knowing about, and an earlier scene meant to leave the very same check true brings
any other check about.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.domain.acquisition import (
    Learned,
    Placement,
    how_learned,
    learned_after,
)
from backend.domain.canon.assertions import Belief
from backend.domain.canon.events import Event
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
# A fact kind someone can only be in one of at a time, so being elsewhere undoes it.
ONE_PLACE_PREDICATES = frozenset({"LOCATED_AT"})

Strength = Literal["hard", "soft"]
# met: true at that point. planned: not recorded, but an earlier scene is meant to
# make it true. not_met: not true at that point. unclear: cannot tell (the entry is
# gone, or the facts' times are too loose). unchecked: written in words.
Status = Literal["met", "planned", "not_met", "unclear", "unchecked"]


KnowingHow = Literal["knows", "seen", "heard"]
# How each way of learning counts: anything for knowing; seeing it for "has seen"; being
# told, overhearing or hearing it said for "has heard".
_COUNTS: dict[str, frozenset[str]] = {
    "seen": frozenset({"saw"}),
    "heard": frozenset({"told", "overheard", "heard_said"}),
}
_HOW_WORDS = {
    "knows": "know this",
    "seen": "have seen this",
    "heard": "have heard this",
}
_LEARNED_WORDS = {
    "told": "was told it",
    "overheard": "overheard it",
    "saw": "saw it",
    "worked_out": "worked it out",
    "heard_said": "heard it said",
    "event": "learned it from something that happened",
    "recorded": "knows it, with no record of how",
}


class KnowsCheck(BaseModel):
    """What the condition's character must know, have seen or have heard: one statement,
    like a fact of the story bible (who it is about, its kind, and what it points at or
    its value)."""

    model_config = ConfigDict(extra="forbid")

    how: KnowingHow = "knows"
    subject_id: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=100)
    other_id: str | None = Field(default=None, min_length=1, max_length=200)
    value: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def _one_end(self) -> KnowsCheck:
        if (self.other_id is None) == (self.value is None):
            raise ValueError("say what it points at, or its value, but not both")
        return self

    def matches(self, subject_id: str, predicate: str, obj: object) -> bool:
        if subject_id != self.subject_id or predicate != self.predicate:
            return False
        if self.other_id is not None:
            return getattr(obj, "entity_id", None) == self.other_id
        value = getattr(obj, "value", None)
        if value is None or isinstance(value, dict):
            return False
        said = str(value).lower() if isinstance(value, bool) else str(value)
        return said.casefold() == (self.value or "").casefold()

    def same_statement(self, other: KnowsCheck) -> bool:
        return (
            self.subject_id == other.subject_id
            and self.predicate == other.predicate
            and self.other_id == other.other_id
            and (self.value or "").casefold() == (other.value or "").casefold()
        )


class SceneCondition(BaseModel):
    """One condition: a check on one entry, what a character knows, or a sentence in the
    author's words."""

    model_config = ConfigDict(extra="forbid")

    when: Literal["before", "after"]
    # hard: the scene needs it (required). soft: the scene would rather have it (preferred).
    strength: Strength = "hard"
    entity_id: str | None = Field(default=None, min_length=1, max_length=200)
    check: Check | None = None
    # In the author's own words, for what the story bible cannot say. Never checked.
    words: str | None = Field(default=None, max_length=MAX_WORDS_LENGTH)
    # What ``entity_id`` (a character) must know, have seen or have heard (STY-005).
    knows: KnowsCheck | None = None

    @model_validator(mode="after")
    def _one_form(self) -> SceneCondition:
        if self.words is not None:
            self.words = self.words.strip() or None
        structured = self.entity_id is not None or self.check is not None or self.knows
        if self.words is not None and structured:
            raise ValueError("a condition is either about an entry or in your own words, not both")
        if self.check is not None and self.knows is not None:
            raise ValueError("a condition is either a fact or what someone knows, not both")
        if self.words is None and (
            self.entity_id is None or (self.check is None and self.knows is None)
        ):
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
    """The condition as a rule about its one entry. Only for a condition about a fact."""
    if condition.entity_id is None or condition.check is None:
        raise ValueError("a condition in words, or about what someone knows, has no rule")
    return RuleBody(applies_to=Selector(entity_id=condition.entity_id), checks=[condition.check])


def statement_words(knows: KnowsCheck, names: dict[str, str]) -> str:
    """The statement a knowing condition is about, as a short clause."""
    from backend.domain.canon.phrasing import phrase

    subject = names.get(knows.subject_id, "Someone no longer in the story bible")
    target = (
        names.get(knows.other_id, "something no longer in the story bible")
        if knows.other_id is not None
        else str(knows.value)
    )
    return f"{subject} {phrase(knows.predicate)} {target}".strip()


def condition_reading(condition: SceneCondition, names: dict[str, str]) -> str:
    if condition.words is not None:
        return condition.words
    if condition.knows is not None:
        who = names.get(condition.entity_id or "", "Someone no longer in the story bible")
        return (
            f"{who} must {_HOW_WORDS[condition.knows.how]}: "
            f"{statement_words(condition.knows, names)}."
        )
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


def _undoes_place(condition: Check, other: Check) -> bool:
    """Whether ``other`` (an earlier "after") leaves someone somewhere else than
    ``condition`` (a "before") needs them: a person is in one place at a time."""
    mine, theirs = condition.pattern, other.pattern
    return (
        condition.kind == "has"
        and other.kind == "has"
        and mine.predicate in ONE_PLACE_PREDICATES
        and theirs.predicate == mine.predicate
        and mine.direction == theirs.direction == "out"
        and mine.other_id is not None
        and theirs.other_id is not None
        and theirs.other_id != mine.other_id
    )


def planned_change(
    condition: SceneCondition, earlier: Sequence[tuple[str, str, Sequence[SceneCondition]]]
) -> PlannedChange | None:
    """What earlier scenes, in reading order, are meant to leave true about this.

    ``earlier`` is (plan id, title, conditions) for each scene before this one, in
    reading order. Only what can be compared exactly is: "has" and "lacks" on the same
    entry and kind of fact; being somewhere else for "is at"; the very same check; and
    for what someone knows, the same statement known (or seen, or heard, the same way).
    """
    for plan_id, title, conditions in reversed(earlier):
        for other in reversed(list(conditions)):
            if other.when != "after" or other.entity_id != condition.entity_id:
                continue
            if condition.knows is not None:
                if other.knows is not None and condition.knows.same_statement(other.knows):
                    if condition.knows.how in ("knows", other.knows.how):
                        return PlannedChange(plan_id, title, True)
                continue
            mine, theirs = condition.check, other.check
            if mine is None or theirs is None:
                continue
            if mine.kind in _OPPOSITE and theirs.pattern == mine.pattern:
                if theirs.kind == mine.kind:
                    return PlannedChange(plan_id, title, True)
                if theirs.kind == _OPPOSITE[mine.kind]:
                    return PlannedChange(plan_id, title, False)
            if _undoes_place(mine, theirs):
                return PlannedChange(plan_id, title, False)
            if mine.kind not in _OPPOSITE and theirs == mine:
                return PlannedChange(plan_id, title, True)
    return None


@dataclass(frozen=True)
class Knowing:
    """What the knowledge conditions are judged against: every belief recorded, the
    events they were learned from, where scenes and events sit, and the statements
    everyone in the story knows (facts marked common), as (subject, kind, other end)."""

    beliefs: Sequence[Belief]
    events: dict[str, Event]
    where: Placement
    common: frozenset[tuple[str, str, str]] = frozenset()
    names: dict[str, str] | None = None


def _end(obj: object) -> str:
    entity = getattr(obj, "entity_id", None)
    if entity is not None:
        return f"@{entity}"
    value = getattr(obj, "value", None)
    return "" if value is None or isinstance(value, dict) else str(value).casefold()


def statement_key(subject_id: str, predicate: str, obj: object) -> tuple[str, str, str]:
    return (subject_id, predicate, _end(obj))


def _knows_key(knows: KnowsCheck) -> tuple[str, str, str]:
    other = f"@{knows.other_id}" if knows.other_id is not None else (knows.value or "").casefold()
    return (knows.subject_id, knows.predicate, other)


def knowing_verdict(plan_id: str, condition: SceneCondition, knowing: Knowing) -> tuple[bool, str]:
    """Whether the condition's character knows (has seen, has heard) the statement where
    the scene comes, and why not in plain words when they do not."""
    knows = condition.knows
    assert knows is not None and condition.entity_id is not None
    names = knowing.names or {}
    who = names.get(condition.entity_id, "They")
    if knows.how == "knows" and _knows_key(knows) in knowing.common:
        return True, ""
    held: list[tuple[Belief, Learned]] = [
        (belief, how_learned(belief, knowing.events))
        for belief in knowing.beliefs
        if belief.holder_entity_id == condition.entity_id
        and belief.closed_revision is None
        and knows.matches(
            belief.proposition.subject_id, belief.proposition.predicate, belief.proposition.object
        )
    ]
    counts = _COUNTS.get(knows.how)
    fitting = [pair for pair in held if counts is None or pair[1].how in counts]

    def in_time(learned: Learned) -> bool:
        if learned_after(learned, plan_id, knowing.where):
            return False
        # Needed before the scene: learned in the scene itself is too late.
        return not (condition.when == "before" and learned.scene_id == plan_id)

    if any(in_time(learned) for _, learned in fitting):
        return True, ""
    if fitting:
        learned = fitting[0][1]
        title = knowing.where.scene_titles.get(learned.scene_id or "")
        place = (
            "only in this scene"
            if learned.scene_id == plan_id
            else f"only later, in “{title}”"
            if title
            else "only later in the story"
        )
        return False, f"{who} learns it {place}."
    if held:
        ways = sorted({_LEARNED_WORDS[learned.how] for _, learned in held})
        wanted = "seeing it" if knows.how == "seen" else "hearing it"
        return False, (
            f"{who} {' and '.join(ways)}, but nothing records {wanted}; record how they "
            "learned it, or change the condition."
        )
    return False, f"Nothing records that {who} knows this."


def condition_state(
    plan_id: str,
    condition: SceneCondition,
    world: World,
    *,
    index: int = 0,
    at_time: tuple[World, World] | None = None,
    planned: PlannedChange | None = None,
    undated: World | None = None,
    knowing: Knowing | None = None,
) -> ConditionState:
    """Whether a condition holds where its scene comes.

    ``world`` is the story as it stands. ``at_time`` is the story at the scene's own
    moment: the facts certainly true then, and those that may be. ``planned`` is what
    an earlier scene is meant to change about it (``planned_change``). ``undated`` is,
    for a scene with no story time, the story without the facts that stop being true
    at some point: when the answer turns on one of those, it cannot be told.
    ``knowing`` is what a condition about what someone knows is judged against.
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
    if condition.knows is not None:
        return _knowing_state(plan_id, condition, knowing, planned, state)
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


def _knowing_state(
    plan_id: str,
    condition: SceneCondition,
    knowing: Knowing | None,
    planned: PlannedChange | None,
    state: Callable[..., ConditionState],
) -> ConditionState:
    if knowing is None:
        return state(None, "unclear", "What they know was not read for this.")
    holds, why = knowing_verdict(plan_id, condition, knowing)
    if holds:
        return state(True, "met")
    if condition.when == "before" and planned is not None and planned.brings_about:
        return state(
            False,
            "planned",
            f"Not recorded yet; “{planned.title}”, earlier in the plan, is meant to bring "
            "it about.",
        )
    return state(False, "not_met", why)
