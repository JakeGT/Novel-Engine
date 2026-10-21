"""Story rules: an author's own continuity rule, as data that can be checked (ADR-013).

A rule says which entries it is about and what must hold for each of them. It is
a small fixed vocabulary, never code: nothing an author or a model supplies is
executed. Evaluation is deterministic over entries, the facts recorded about them
(with the story time each is true for) and what characters believe, so the same
story always gives the same answer.

A rule carries examples: tiny made-up stories it should catch and ones it should
allow. They run through the same evaluator as the real story, so what a rule is
shown to do and what it does cannot differ.

What a rule can say (CON-005, CAN-013): must have, must not have, at most or at
least so many of a kind of fact; a value within a range ("age from 0 to 120"); if
it has one fact it must also have another; it cannot have both of two facts; it
must (or must not) believe something. A rule may name exceptions: entries it does
not apply to, or a fact or tag that excuses an entry. And a rule is judged over
every fact as recorded (the default, and all a rule could do before), at every
moment of story time, or at the end of the story.

Where story times are too loose to tell whether two facts overlap, the answer is
"cannot tell" rather than a guess, in the strong three-valued (Kleene) sense:
knowing the times better can only turn "cannot tell" into yes or no, never turn a
yes into a no. Only a definite break can block a change; "cannot tell" only warns.

The form of a rule is versioned (``RuleBody.form``): a rule kept in an older form
means what it meant, and a rule in a form newer than this code reads is refused.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.canon.marks import TAGS_KEY, clean_tags
from backend.domain.canon.naming import normalize_name
from backend.domain.operations import (
    AmendEventOp,
    AmendFactOp,
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    MergeEntitiesOp,
    RestoreEntityOp,
    RetireEntityOp,
    RetireEventOp,
    UpdateEntityOp,
)
from backend.domain.validation import RuleEnforcement
from backend.domain.worldtime import (
    LIMIT,
    At,
    Between,
    Interval,
    Relative,
    Span,
    Truth,
    Unknown,
    number_words,
    resolve,
    true_at,
)

MAX_RULES = 100
MAX_TITLE_LENGTH = 160
MAX_SENTENCE_LENGTH = 600
MAX_PATTERNS = 4
MAX_CHECKS = 4
MAX_EXCEPTIONS = 20
MAX_EXAMPLES = 8
MAX_EXAMPLE_ENTRIES = 12
MAX_EXAMPLE_FACTS = 24
MAX_EXAMPLE_BELIEFS = 12
MAX_COUNT = 1000
MAX_VIOLATIONS = 200

# The newest form of the rule language this code reads. Form 1 is has, lacks, at
# most and at least over entries and the facts recorded about them; form 2 adds
# ranges, implications, incompatibilities, beliefs, exceptions and story time.
RULE_FORM = 2

CheckKind = Literal[
    "has",
    "lacks",
    "at_most",
    "at_least",
    "between",
    "implies",
    "not_both",
    "believes",
    "does_not_believe",
]
COUNT_KINDS = frozenset({"has", "lacks", "at_most", "at_least"})
BELIEF_KINDS = frozenset({"believes", "does_not_believe"})
# as_recorded: every fact recorded, whatever its story time (form 1's meaning).
# every_moment: at each moment of story time, the facts true then.
# at_end: the facts still true at the end of the story.
RuleTime = Literal["as_recorded", "every_moment", "at_end"]


class Pattern(BaseModel):
    """A kind of fact about the entry being checked."""

    model_config = ConfigDict(extra="forbid")

    predicate: str = Field(min_length=1, max_length=100, description="An allowed kind of fact")
    direction: Literal["out", "in"] = Field(
        default="out",
        description="out: the entry is what the fact is about. in: the fact points at the entry",
    )
    other_type: str | None = Field(
        default=None,
        max_length=100,
        description="Type of the entry at the other end, if it matters",
    )
    other_id: str | None = Field(
        default=None, max_length=200, description="One specific entry at the other end"
    )
    value: str | None = Field(
        default=None, max_length=300, description="For a fact that holds a value: that value"
    )


class Check(BaseModel):
    """One thing that must hold for each entry the rule is about.

    has, lacks, at_most, at_least: how many facts like ``pattern`` it has.
    between: every value of ``pattern`` is a number from ``low`` to ``high``.
    implies: if it has ``if_has``, it must also have ``pattern``.
    not_both: it cannot have both ``pattern`` and ``not_with``.
    believes, does_not_believe: it holds (or holds no) belief like ``pattern``,
    about ``about_id`` or, when that is empty, about itself.
    """

    model_config = ConfigDict(extra="forbid")

    kind: CheckKind
    pattern: Pattern
    count: int = Field(default=1, ge=0, le=MAX_COUNT, description="For at_most and at_least")
    low: float | None = Field(
        default=None, ge=-LIMIT, le=LIMIT, description="For between: the smallest value allowed"
    )
    high: float | None = Field(
        default=None, ge=-LIMIT, le=LIMIT, description="For between: the largest value allowed"
    )
    if_has: Pattern | None = Field(
        default=None, description="For implies: the fact that, when present, requires pattern"
    )
    not_with: Pattern | None = Field(
        default=None, description="For not_both: the fact that cannot stand with pattern"
    )
    about_id: str | None = Field(
        default=None,
        max_length=200,
        description="For believes: the entry the belief is about; empty means the entry itself",
    )

    @model_validator(mode="after")
    def _shape(self) -> Check:
        kind = self.kind
        if kind == "between":
            if self.low is None and self.high is None:
                raise ValueError("a range needs a smallest value, a largest value, or both")
            if self.low is not None and self.high is not None and self.low > self.high:
                raise ValueError("the smallest value of a range is larger than the largest")
            pattern = self.pattern
            if (
                pattern.direction != "out"
                or pattern.value is not None
                or pattern.other_id is not None
                or pattern.other_type is not None
            ):
                raise ValueError("a range is about a value the entry has, with nothing else set")
        elif self.low is not None or self.high is not None:
            raise ValueError("only a range has a smallest or largest value")
        if (kind == "implies") != (self.if_has is not None):
            raise ValueError("an “if it has … it must also have …” needs both facts, and only it")
        if (kind == "not_both") != (self.not_with is not None):
            raise ValueError("a “cannot have both” needs both facts, and only it")
        if self.about_id is not None and kind not in BELIEF_KINDS:
            raise ValueError("only a belief is about another entry")
        return self


class Selector(BaseModel):
    """Which entries a rule is about. Every part given must match."""

    model_config = ConfigDict(extra="forbid")

    type: str | None = Field(default=None, max_length=100)
    tag: str | None = Field(default=None, max_length=60)
    entity_id: str | None = Field(default=None, max_length=200)
    having: list[Pattern] = Field(default_factory=list, max_length=MAX_PATTERNS)


class Exceptions(BaseModel):
    """Entries a rule does not apply to. Any one part matching is enough."""

    model_config = ConfigDict(extra="forbid")

    entity_ids: list[str] = Field(
        default_factory=list, max_length=MAX_EXCEPTIONS, description="Named entries"
    )
    tag: str | None = Field(default=None, max_length=60, description="Entries with this tag")
    having: list[Pattern] = Field(
        default_factory=list,
        max_length=MAX_PATTERNS,
        description="Entries that have any of these facts",
    )

    def empty(self) -> bool:
        return not self.entity_ids and not self.tag and not self.having


class RuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Which form of the rule language this is written in; set from what it uses.
    form: int = Field(default=1, ge=1)
    applies_to: Selector = Field(default_factory=Selector)
    # all: every check must hold. any: one is enough.
    mode: Literal["all", "any"] = "all"
    checks: list[Check] = Field(min_length=1, max_length=MAX_CHECKS)
    except_for: Exceptions = Field(default_factory=Exceptions)
    time: RuleTime = "as_recorded"

    @model_validator(mode="after")
    def _form(self) -> RuleBody:
        if self.form > RULE_FORM:
            raise ValueError(
                "this rule was written for a newer version of Novel Engine and cannot be read here"
            )
        self.form = max(self.form, needed_form(self))
        return self


def needed_form(body: RuleBody) -> int:
    """The oldest form of the rule language that can say this rule."""
    if (
        body.time != "as_recorded"
        or not body.except_for.empty()
        or any(check.kind not in COUNT_KINDS for check in body.checks)
    ):
        return 2
    return 1


def uses_beliefs(body: RuleBody) -> bool:
    return any(check.kind in BELIEF_KINDS for check in body.checks)


class ExampleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1, max_length=60)
    type: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=6)


class ExampleFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_ref: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=100)
    object_ref: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=300)
    # When it is true, on the story's own number line: from ``starts`` until ``ends``.
    starts: float | None = Field(default=None, ge=-LIMIT, le=LIMIT)
    ends: float | None = Field(default=None, ge=-LIMIT, le=LIMIT)


class ExampleBelief(BaseModel):
    """Something a made-up character believes, which may or may not be so."""

    model_config = ConfigDict(extra="forbid")

    holder_ref: str = Field(min_length=1, max_length=200)
    subject_ref: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=100)
    object_ref: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=300)


class RuleExample(BaseModel):
    """A tiny made-up story, and whether the rule should catch it."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=300)
    entries: list[ExampleEntry] = Field(default_factory=list, max_length=MAX_EXAMPLE_ENTRIES)
    facts: list[ExampleFact] = Field(default_factory=list, max_length=MAX_EXAMPLE_FACTS)
    beliefs: list[ExampleBelief] = Field(default_factory=list, max_length=MAX_EXAMPLE_BELIEFS)
    expect: Literal["caught", "allowed"]


class RuleStatus(StrEnum):
    ON = "on"
    PAUSED = "paused"


@dataclass(frozen=True)
class StoryRule:
    workspace_id: str
    project_id: str
    id: str
    # A short name for the rule, and the sentence the author wrote it as.
    title: str
    sentence: str
    status: RuleStatus
    # The current version. Earlier versions are kept, never changed.
    version: int
    body: RuleBody
    examples: list[RuleExample]
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    # Warn (the default) or block a change that would newly break it (ADR-013, CON-015).
    enforcement: RuleEnforcement = RuleEnforcement.WARNING


# ------------------------------------------------------------------ the world
StoryTime = Unknown | At | Between | Relative


@dataclass(frozen=True)
class WorldEntry:
    id: str
    type: str
    name: str
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class WorldFact:
    id: str
    subject_id: str
    predicate: str
    object_id: str | None = None
    value: str | None = None
    # Story time (ADR-020): when it starts and stops being true. None is open.
    starts: StoryTime | None = field(default=None, hash=False)
    ends: StoryTime | None = field(default=None, hash=False)


@dataclass(frozen=True)
class WorldBelief:
    id: str
    holder_id: str
    subject_id: str
    predicate: str
    object_id: str | None = None
    value: str | None = None


@dataclass
class World:
    """Entries, the facts recorded about them, beliefs, and event times. All a rule sees."""

    entries: dict[str, WorldEntry] = field(default_factory=dict)
    facts: dict[str, WorldFact] = field(default_factory=dict)
    beliefs: dict[str, WorldBelief] = field(default_factory=dict)
    # Each event's time, so a fact timed from an event can be placed.
    events: dict[str, StoryTime | None] = field(default_factory=dict)
    # Entries retired from the story, so one brought back (undoing a merge) is seen again.
    retired: dict[str, WorldEntry] = field(default_factory=dict)

    def copy(self) -> World:
        return World(
            entries=dict(self.entries),
            facts=dict(self.facts),
            beliefs=dict(self.beliefs),
            events=dict(self.events),
            retired=dict(self.retired),
        )


def _tags(attributes: dict[str, object]) -> frozenset[str]:
    raw = attributes.get(TAGS_KEY)
    return frozenset(
        normalize_name(tag) for tag in clean_tags(raw if isinstance(raw, list) else [])
    )


def _scalar(obj: object) -> str | None:
    value = getattr(obj, "value", None)
    if value is None or isinstance(value, dict):
        return None
    return str(value).lower() if isinstance(value, bool) else str(value)


def _time(when: object) -> StoryTime | None:
    """A stored time, with "unknown" meaning no time at all, as the commit stores it."""
    if when is None or isinstance(when, Unknown):
        return None
    return when  # type: ignore[return-value]


def _world_belief(belief_id: str, holder_id: str, proposition: object) -> WorldBelief:
    obj = getattr(proposition, "object", None)
    return WorldBelief(
        id=belief_id,
        holder_id=holder_id,
        subject_id=proposition.subject_id,  # type: ignore[attr-defined]
        predicate=proposition.predicate,  # type: ignore[attr-defined]
        object_id=getattr(obj, "entity_id", None),
        value=_scalar(obj),
    )


def world_from_canon(
    entities: Iterable[CanonEntity],
    facts: Iterable[Fact],
    beliefs: Iterable[Belief] = (),
    events: Iterable[Event] = (),
) -> World:
    world = World()
    for entity in entities:
        entry = WorldEntry(entity.id, entity.type, entity.name, _tags(entity.attributes))
        if entity.is_active:
            world.entries[entity.id] = entry
        else:
            world.retired[entity.id] = entry
    for fact in facts:
        if fact.closed_revision is not None:
            continue
        obj = fact.proposition.object
        world.facts[fact.id] = WorldFact(
            id=fact.id,
            subject_id=fact.proposition.subject_id,
            predicate=fact.proposition.predicate,
            object_id=getattr(obj, "entity_id", None),
            value=_scalar(obj),
            starts=_time(fact.starts),
            ends=_time(fact.ends),
        )
    for belief in beliefs:
        if belief.closed_revision is None:
            world.beliefs[belief.id] = _world_belief(
                belief.id, belief.holder_entity_id, belief.proposition
            )
    for event in events:
        if event.retired_revision is None:
            world.events[event.id] = event.when
    return world


def apply_operations(world: World, operations: Sequence[CanonOperation]) -> World:
    """The world as it would be once these changes were kept. Unknown kinds change nothing."""
    after = world.copy()
    for operation in operations:
        if isinstance(operation, CreateEntityOp):
            after.entries[operation.id] = WorldEntry(
                operation.id, operation.type, operation.name, _tags(operation.attributes)
            )
        elif isinstance(operation, UpdateEntityOp):
            current = after.entries.get(operation.id)
            if current is not None:
                after.entries[operation.id] = WorldEntry(
                    current.id,
                    operation.type or current.type,
                    operation.name or current.name,
                    current.tags if operation.attributes is None else _tags(operation.attributes),
                )
        elif isinstance(operation, RetireEntityOp):
            gone = after.entries.pop(operation.id, None)
            if gone is not None:
                after.retired[operation.id] = gone
        elif isinstance(operation, RestoreEntityOp):
            back = after.retired.pop(operation.id, None)
            if back is not None:
                after.entries[operation.id] = back
        elif isinstance(operation, AssertFactOp):
            obj = operation.proposition.object
            after.facts[operation.id] = WorldFact(
                id=operation.id,
                subject_id=operation.proposition.subject_id,
                predicate=operation.proposition.predicate,
                object_id=getattr(obj, "entity_id", None),
                value=_scalar(obj),
                starts=_time(operation.starts),
                ends=_time(operation.ends),
            )
        elif isinstance(operation, AmendFactOp):
            fact = after.facts.get(operation.id)
            if fact is not None:
                after.facts[operation.id] = WorldFact(
                    fact.id,
                    fact.subject_id,
                    fact.predicate,
                    fact.object_id,
                    fact.value,
                    starts=fact.starts if operation.starts is None else _time(operation.starts),
                    ends=fact.ends if operation.ends is None else _time(operation.ends),
                )
        elif isinstance(operation, CloseFactOp):
            after.facts.pop(operation.id, None)
        elif isinstance(operation, AssertBeliefOp):
            after.beliefs[operation.id] = _world_belief(
                operation.id, operation.holder_entity_id, operation.proposition
            )
        elif isinstance(operation, CloseBeliefOp):
            after.beliefs.pop(operation.id, None)
        elif isinstance(operation, CreateEventOp):
            after.events[operation.id] = operation.when
        elif isinstance(operation, AmendEventOp):
            if operation.when is not None and operation.id in after.events:
                after.events[operation.id] = operation.when
        elif isinstance(operation, RetireEventOp):
            after.events.pop(operation.id, None)
        elif isinstance(operation, MergeEntitiesOp):
            # What names the merged entry names the kept one (backend/domain/merge.py).
            merged, kept = operation.id, operation.into
            gone = after.entries.pop(merged, None)
            if gone is not None:
                after.retired[merged] = gone

            def swap(entry_id: str | None, merged: str = merged, kept: str = kept) -> str | None:
                return kept if entry_id == merged else entry_id

            for fact_id, fact in list(after.facts.items()):
                if merged not in (fact.subject_id, fact.object_id):
                    continue
                if fact_id in operation.drop_facts:
                    after.facts.pop(fact_id)
                    continue
                after.facts[fact_id] = replace(
                    fact, subject_id=swap(fact.subject_id) or kept, object_id=swap(fact.object_id)
                )
            for belief_id, belief in list(after.beliefs.items()):
                if merged in (belief.holder_id, belief.subject_id, belief.object_id):
                    after.beliefs[belief_id] = replace(
                        belief,
                        holder_id=swap(belief.holder_id) or kept,
                        subject_id=swap(belief.subject_id) or kept,
                        object_id=swap(belief.object_id),
                    )
    return after


def world_from_example(example: RuleExample) -> World:
    world = World()
    for entry in example.entries:
        world.entries[entry.ref] = WorldEntry(
            entry.ref,
            entry.type,
            entry.name,
            frozenset(normalize_name(tag) for tag in clean_tags(list(entry.tags))),
        )
    for number, fact in enumerate(example.facts):
        world.facts[f"example-{number}"] = WorldFact(
            id=f"example-{number}",
            subject_id=fact.subject_ref,
            predicate=fact.predicate,
            object_id=fact.object_ref if fact.object_ref in world.entries else None,
            value=fact.value,
            starts=At(at=fact.starts) if fact.starts is not None else None,
            ends=At(at=fact.ends) if fact.ends is not None else None,
        )
    for number, belief in enumerate(example.beliefs):
        world.beliefs[f"example-belief-{number}"] = WorldBelief(
            id=f"example-belief-{number}",
            holder_id=belief.holder_ref,
            subject_id=belief.subject_ref,
            predicate=belief.predicate,
            object_id=belief.object_ref if belief.object_ref in world.entries else None,
            value=belief.value,
        )
    return world


# ----------------------------------------------------------------- evaluation
@dataclass(frozen=True)
class Violation:
    rule_id: str
    entity_id: str
    entity_name: str
    # False when the story's times are too loose to tell: it may not hold. Never blocks.
    certain: bool = True
    # When it does not hold, in words ("at 12 in story time", "at the end of the
    # story"); empty for a rule judged over every fact as recorded.
    when: str = ""


class FactIndex:
    """The facts of one world looked up by the entry they are about or point at.

    Built once per world, so checking a rule costs a lookup per entry instead of a
    pass over every fact. Build it after the world is complete: it does not follow
    later changes to the world.
    """

    def __init__(self, world: World) -> None:
        self.outgoing: dict[tuple[str, str], list[WorldFact]] = {}
        self.incoming: dict[tuple[str, str], list[WorldFact]] = {}
        self.touching: dict[str, list[WorldFact]] = {}
        # Beliefs by who holds them, and by the entries they are about or name.
        self.held: dict[str, list[WorldBelief]] = {}
        self.believed_about: dict[str, list[WorldBelief]] = {}
        for fact in world.facts.values():
            self.outgoing.setdefault((fact.subject_id, fact.predicate), []).append(fact)
            self.touching.setdefault(fact.subject_id, []).append(fact)
            if fact.object_id is not None:
                self.incoming.setdefault((fact.object_id, fact.predicate), []).append(fact)
                if fact.object_id != fact.subject_id:
                    self.touching.setdefault(fact.object_id, []).append(fact)
        for belief in world.beliefs.values():
            self.held.setdefault(belief.holder_id, []).append(belief)
            self.believed_about.setdefault(belief.subject_id, []).append(belief)
            if belief.object_id is not None and belief.object_id != belief.subject_id:
                self.believed_about.setdefault(belief.object_id, []).append(belief)
        self._events = world.events
        self._spans: dict[str, Span] = {}

    def _when_of(self, event_id: str) -> StoryTime | None | Literal[False]:
        return self._events[event_id] if event_id in self._events else False

    def places(self, when: StoryTime) -> bool:
        """Whether a time can be put anywhere in story time (its events are timed)."""
        return resolve(when, self._when_of).interval is not None

    def _bound(self, when: StoryTime | None) -> Interval | None:
        if when is None or isinstance(when, Unknown):
            return None
        # A start or end that is set but cannot be placed (timed from an event with
        # no time, a missing event, or a loop) could be at any time: it is not absent.
        found = resolve(when, self._when_of).interval
        return found if found is not None else Interval(-math.inf, math.inf)

    def span(self, fact: WorldFact) -> Span:
        """When the fact is true, with times relative to events followed."""
        found = self._spans.get(fact.id)
        if found is None:
            start, end = self._bound(fact.starts), self._bound(fact.ends)
            found = Span(start=start, end=end, stated=start is not None or end is not None)
            self._spans[fact.id] = found
        return found


# How much a fact counts where the rule is being judged: surely, perhaps, or not.
Weigh = Callable[[WorldFact], Truth]
# A three-valued answer: True, False, or None for "cannot tell" (strong Kleene).
Tri = bool | None


def _always(_fact: WorldFact) -> Truth:
    return Truth.TRUE


def _not(value: Tri) -> Tri:
    return None if value is None else not value


def _all(values: Iterable[Tri]) -> Tri:
    unsure = False
    for value in values:
        if value is False:
            return False
        if value is None:
            unsure = True
    return None if unsure else True


def _any(values: Iterable[Tri]) -> Tri:
    unsure = False
    for value in values:
        if value is True:
            return True
        if value is None:
            unsure = True
    return None if unsure else False


def _other_fits(world: World, pattern: Pattern, other_id: str | None, value: str | None) -> bool:
    # A fact about an entry that is gone does not count.
    other = world.entries.get(other_id) if other_id is not None else None
    if other_id is not None and other is None:
        return False
    if pattern.other_id is not None and other_id != pattern.other_id:
        return False
    if pattern.other_type is not None and (other is None or other.type != pattern.other_type):
        return False
    return pattern.value is None or (
        value is not None and normalize_name(value) == normalize_name(pattern.value)
    )


def _facts(world: World, index: FactIndex, entry_id: str, pattern: Pattern) -> list[WorldFact]:
    """The entry's facts of this shape."""
    outgoing = pattern.direction == "out"
    return [
        fact
        for fact in (index.outgoing if outgoing else index.incoming).get(
            (entry_id, pattern.predicate), ()
        )
        if _other_fits(world, pattern, fact.object_id if outgoing else fact.subject_id, fact.value)
    ]


def _matches(world: World, index: FactIndex, entry_id: str, pattern: Pattern) -> int:
    """How many facts of this shape the entry has, whatever their time."""
    return len(_facts(world, index, entry_id, pattern))


def _count(
    world: World, index: FactIndex, entry_id: str, pattern: Pattern, weigh: Weigh
) -> tuple[int, int]:
    """How many facts of this shape surely hold, and how many may."""
    sure = maybe = 0
    for fact in _facts(world, index, entry_id, pattern):
        truth = weigh(fact)
        if truth is Truth.TRUE:
            sure += 1
            maybe += 1
        elif truth is Truth.MAYBE:
            maybe += 1
    return sure, maybe


def _has(world: World, index: FactIndex, entry_id: str, pattern: Pattern, weigh: Weigh) -> Tri:
    sure, maybe = _count(world, index, entry_id, pattern, weigh)
    if sure > 0:
        return True
    return False if maybe == 0 else None


def _beliefs(world: World, index: FactIndex, entry_id: str, check: Check) -> int:
    """How many beliefs like the check's the entry holds."""
    pattern = check.pattern
    subject = check.about_id or entry_id
    if subject not in world.entries:
        return 0
    found = 0
    for belief in index.held.get(entry_id, ()):
        if belief.predicate != pattern.predicate:
            continue
        if pattern.direction == "out":
            if belief.subject_id != subject:
                continue
            other_id = belief.object_id
        else:
            if belief.object_id != subject:
                continue
            other_id = belief.subject_id
        if _other_fits(world, pattern, other_id, belief.value):
            found += 1
    return found


# A number at the start of a value, with thousands separators and decimals ("64 years",
# "1,200 leagues", "2.5 m"); not one run on into more digits ("3/4", "12:30", "1,23").
_LEADING_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![.,/:]?\d)")


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    try:
        number = float(text)
    except ValueError:
        match = _LEADING_NUMBER.match(text)
        if match is None:
            return None
        number = float(match.group().replace(",", ""))
    return number if math.isfinite(number) else None


def _check(world: World, index: FactIndex, entry_id: str, check: Check, weigh: Weigh) -> Tri:
    kind = check.kind
    if kind in BELIEF_KINDS:
        held = _beliefs(world, index, entry_id, check) > 0
        return held if kind == "believes" else not held
    if kind == "between":
        result: Tri = True
        for fact in _facts(world, index, entry_id, check.pattern):
            number = _number(fact.value)
            inside = (
                number is not None
                and (check.low is None or number >= check.low)
                and (check.high is None or number <= check.high)
            )
            if inside:
                continue
            truth = weigh(fact)
            if truth is Truth.FALSE:
                continue
            # A value that cannot be read as a number may still be inside the range: it
            # can only make the rule uncertain, never broken for sure.
            if number is None or truth is Truth.MAYBE:
                result = None
                continue
            return False
        return result
    if kind == "implies":
        assert check.if_has is not None
        return _any(
            [
                _not(_has(world, index, entry_id, check.if_has, weigh)),
                _has(world, index, entry_id, check.pattern, weigh),
            ]
        )
    if kind == "not_both":
        assert check.not_with is not None
        return _not(
            _all(
                [
                    _has(world, index, entry_id, check.pattern, weigh),
                    _has(world, index, entry_id, check.not_with, weigh),
                ]
            )
        )
    sure, maybe = _count(world, index, entry_id, check.pattern, weigh)
    if kind == "has":
        return True if sure > 0 else (False if maybe == 0 else None)
    if kind == "lacks":
        return True if maybe == 0 else (False if sure > 0 else None)
    if kind == "at_most":
        return True if maybe <= check.count else (False if sure > check.count else None)
    return True if sure >= check.count else (False if maybe < check.count else None)


def _candidates(
    world: World, selector: Selector, exceptions: Exceptions, only: Iterable[str] | None
) -> list[WorldEntry]:
    """Entries the rule may be about by what never changes: type, tag, which entry."""
    pool: Iterable[WorldEntry] = (
        world.entries.values()
        if only is None
        else [world.entries[item] for item in set(only) if item in world.entries]
    )
    named = set(exceptions.entity_ids)
    excused_tag = normalize_name(exceptions.tag) if exceptions.tag else None
    chosen: list[WorldEntry] = []
    for entry in pool:
        if selector.entity_id is not None and entry.id != selector.entity_id:
            continue
        if selector.type is not None and entry.type != selector.type:
            continue
        if selector.tag is not None and normalize_name(selector.tag) not in entry.tags:
            continue
        if entry.id in named or (excused_tag is not None and excused_tag in entry.tags):
            continue
        chosen.append(entry)
    return sorted(chosen, key=lambda item: (item.name.casefold(), item.id))


def _in_scope(world: World, index: FactIndex, body: RuleBody, entry_id: str, weigh: Weigh) -> Tri:
    """Whether the rule is about this entry here: its facts match, and nothing excuses it."""
    having = _all(_has(world, index, entry_id, p, weigh) for p in body.applies_to.having)
    excused = _any(_has(world, index, entry_id, p, weigh) for p in body.except_for.having)
    return _all([having, _not(excused)])


def _breaks(world: World, index: FactIndex, body: RuleBody, entry_id: str, weigh: Weigh) -> Tri:
    """True: the rule is about the entry and does not hold. None: cannot tell."""
    scope = _in_scope(world, index, body, entry_id, weigh)
    if scope is False:
        return False
    results = [_check(world, index, entry_id, check, weigh) for check in body.checks]
    holds = _all(results) if body.mode == "all" else _any(results)
    return _all([scope, _not(holds)])


def _fact_patterns(body: RuleBody) -> list[Pattern]:
    found = [*body.applies_to.having, *body.except_for.having]
    for check in body.checks:
        if check.kind in BELIEF_KINDS:
            continue
        found.append(check.pattern)
        found.extend(item for item in (check.if_has, check.not_with) if item is not None)
    return found


def _at(moment: float, index: FactIndex) -> Weigh:
    point = Interval(moment, moment)
    return lambda fact: true_at(index.span(fact), point)


def _at_end(index: FactIndex) -> Weigh:
    """Whether a fact is still true at the end of the story: it has not stopped."""

    def weigh(fact: WorldFact) -> Truth:
        if fact.ends is None:
            return Truth.TRUE
        # An end that cannot be placed (its event is gone, or a loop) may or may not
        # have come; one that can, has, since the story ends after every time in it.
        return Truth.FALSE if index.places(fact.ends) else Truth.MAYBE

    return weigh


def _moments(world: World, index: FactIndex, body: RuleBody, entry_id: str) -> list[float]:
    """One moment in each stretch of story time where the entry's facts stay the same.

    Truth at a moment changes only at an edge of a fact's span (an edge's earliest
    or latest), so a moment just before the first edge and each edge itself cover
    every stretch. No edges means one moment says it all.
    """
    predicates = {pattern.predicate for pattern in _fact_patterns(body)}
    edges: set[float] = set()
    for fact in index.touching.get(entry_id, ()):
        if fact.predicate not in predicates or (fact.starts is None and fact.ends is None):
            continue
        span = index.span(fact)
        for interval in (span.start, span.end):
            if interval is None:
                continue
            for edge in (interval.lo, interval.hi):
                if math.isfinite(edge):
                    edges.add(edge)
    if not edges:
        return [0.0]
    ordered = sorted(edges)
    return [ordered[0] - 1, *ordered]


def _judge(world: World, index: FactIndex, body: RuleBody, entry: WorldEntry) -> tuple[Tri, str]:
    """Whether the rule breaks for this entry, over the time the rule speaks of."""
    if body.time == "as_recorded":
        return _breaks(world, index, body, entry.id, _always), ""
    if body.time == "at_end":
        return _breaks(world, index, body, entry.id, _at_end(index)), "at the end of the story"
    unsure: str | None = None
    for moment in _moments(world, index, body, entry.id):
        found = _breaks(world, index, body, entry.id, _at(moment, index))
        if found is True:
            return True, f"at {number_words(moment)} in story time"
        if found is None and unsure is None:
            unsure = f"around {number_words(moment)} in story time"
    return (None, unsure) if unsure is not None else (False, "")


def _selected(
    world: World,
    index: FactIndex,
    body: RuleBody,
    only: Iterable[str] | None = None,
) -> list[WorldEntry]:
    """Entries the rule may be about, judged over every fact as recorded."""
    return [
        entry
        for entry in _candidates(world, body.applies_to, body.except_for, only)
        if _in_scope(world, index, body, entry.id, _always) is not False
    ]


def selected_entries(
    body: RuleBody, world: World, only: Iterable[str], *, index: FactIndex | None = None
) -> list[WorldEntry]:
    """Which of ``only`` the rule is about in this world, whether or not it holds for them."""
    return _selected(world, index if index is not None else FactIndex(world), body, only)


def violations(
    rule_id: str,
    body: RuleBody,
    world: World,
    *,
    limit: int | None = MAX_VIOLATIONS,
    index: FactIndex | None = None,
    only: Iterable[str] | None = None,
) -> list[Violation]:
    """Every entry the rule is about for which it does not, or may not, hold.

    ``limit`` caps how many are returned, for showing; pass None to get all of them,
    as deciding whether a change newly breaks a rule must. ``only`` restricts the
    answer to those entries. A rule judged over every fact as recorded is always
    certain; one judged in story time may not be, where times are too loose.
    """
    index = index if index is not None else FactIndex(world)
    found: list[Violation] = []
    for entry in _candidates(world, body.applies_to, body.except_for, only):
        broken, when = _judge(world, index, body, entry)
        if broken is False:
            continue
        found.append(Violation(rule_id, entry.id, entry.name, certain=broken is True, when=when))
        if limit is not None and len(found) >= limit:
            break
    return found


_WORLD_CHANGES = (
    CreateEntityOp,
    UpdateEntityOp,
    RetireEntityOp,
    RestoreEntityOp,
    MergeEntitiesOp,
    AssertFactOp,
    AmendFactOp,
    CloseFactOp,
    AssertBeliefOp,
    CloseBeliefOp,
    CreateEventOp,
    AmendEventOp,
    RetireEventOp,
)
_TIME_CHANGES = (CreateEventOp, AmendEventOp, RetireEventOp)


def changes_world(operations: Sequence[CanonOperation]) -> bool:
    """Whether any of these changes alters what a rule can see."""
    return any(isinstance(operation, _WORLD_CHANGES) for operation in operations)


def moves_time(operations: Sequence[CanonOperation]) -> bool:
    """Whether these changes may move a fact in story time through an event's time.

    A fact can be timed from an event, which can be timed from another, so such a
    change may reach any entry: a rule judged in story time is then worked out for all.
    """
    return any(isinstance(operation, _TIME_CHANGES) for operation in operations)


def affected_entries(
    before: World,
    after: World,
    operations: Sequence[CanonOperation],
    *,
    indexes: tuple[FactIndex, FactIndex] | None = None,
) -> set[str]:
    """Every entry whose standing under any rule these changes could alter.

    An entry's standing depends only on the entry itself, the facts about or
    pointing at it, the beliefs it holds, the entries at the other end of those, and
    (for a rule in story time) event times, which ``moves_time`` answers for. So it
    can change only for an entry a change names, an end of a fact added, amended or
    ended, the holder of a belief added or ended, or a neighbour of an entry that
    was added, changed or removed.
    """
    before_index, after_index = indexes or (FactIndex(before), FactIndex(after))
    affected: set[str] = set()

    def ends(fact: WorldFact | None) -> None:
        if fact is not None:
            affected.add(fact.subject_id)
            if fact.object_id is not None:
                affected.add(fact.object_id)

    for operation in operations:
        if isinstance(operation, MergeEntitiesOp):
            for entry_id in (operation.id, operation.into):
                affected.add(entry_id)
                for index in (before_index, after_index):
                    for fact in index.touching.get(entry_id, ()):
                        ends(fact)
                    for belief in index.believed_about.get(entry_id, ()):
                        affected.add(belief.holder_id)
        elif isinstance(
            operation, (CreateEntityOp, UpdateEntityOp, RetireEntityOp, RestoreEntityOp)
        ):
            affected.add(operation.id)
            for index in (before_index, after_index):
                for fact in index.touching.get(operation.id, ()):
                    ends(fact)
                for belief in index.believed_about.get(operation.id, ()):
                    affected.add(belief.holder_id)
        elif isinstance(operation, (AssertFactOp, CloseFactOp, AmendFactOp)):
            ends(before.facts.get(operation.id))
            ends(after.facts.get(operation.id))
            proposition = getattr(operation, "proposition", None)
            if proposition is not None:
                affected.add(proposition.subject_id)
                other = getattr(proposition.object, "entity_id", None)
                if other is not None:
                    affected.add(other)
        elif isinstance(operation, (AssertBeliefOp, CloseBeliefOp)):
            for held in (before.beliefs.get(operation.id), after.beliefs.get(operation.id)):
                if held is not None:
                    affected.add(held.holder_id)
            holder = getattr(operation, "holder_entity_id", None)
            if holder is not None:
                affected.add(holder)
    return affected


@dataclass(frozen=True)
class ExampleResult:
    description: str
    expect: str
    # What the rule actually does with it.
    outcome: str
    agrees: bool


def run_examples(body: RuleBody, examples: Sequence[RuleExample]) -> list[ExampleResult]:
    results: list[ExampleResult] = []
    for example in examples:
        caught = bool(violations("example", body, world_from_example(example)))
        outcome = "caught" if caught else "allowed"
        results.append(
            ExampleResult(example.description, example.expect, outcome, outcome == example.expect)
        )
    return results


# ------------------------------------------------------------------- reading
# The built-in kinds of fact as the verb of a sentence. Others read as their own words.
_VERBS = {
    "LOCATED_AT": "is located at",
    "OWNS": "owns",
    "MEMBER_OF": "is a member of",
    "PARTICIPATED_IN": "took part in",
    "KNOWS": "knows",
    "RELATED_TO": "is related to",
    "CAUSED": "caused",
    "HAS_NAME": "is named",
    "HAS_STATUS": "has the status",
    "HAS_DETAIL": "has the detail",
}


def _words(identifier: str) -> str:
    return identifier.replace("_", " ").strip().lower()


def _verb(predicate: str) -> str:
    return _VERBS.get(predicate, _words(predicate))


def _other(pattern: Pattern, names: dict[str, str]) -> str:
    if pattern.other_id is not None:
        return names.get(pattern.other_id, "an entry that is no longer there")
    if pattern.value is not None:
        return f"“{pattern.value}”"
    if pattern.other_type is not None:
        kind = _words(pattern.other_type)
        return f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind}"
    return "something"


def _phrase(pattern: Pattern, names: dict[str, str], subject: str = "it") -> str:
    other = _other(pattern, names)
    if pattern.direction == "out":
        return f"{subject} {_verb(pattern.predicate)} {other}"
    return f"{other} {_verb(pattern.predicate)} {subject}"


def _listed(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _range(check: Check) -> str:
    low = number_words(check.low) if check.low is not None else None
    high = number_words(check.high) if check.high is not None else None
    if low is not None and high is not None:
        return f"a number from {low} to {high}"
    return f"a number of at least {low}" if low is not None else f"a number of at most {high}"


def _read_check(check: Check, names: dict[str, str]) -> str:
    phrase = _phrase(check.pattern, names)
    kind = check.kind
    if kind == "has":
        return f"must have this: {phrase}"
    if kind == "lacks":
        return f"must not have this: {phrase}"
    if kind == "at_most":
        return f"can have at most {check.count} of this: {phrase}"
    if kind == "at_least":
        return f"needs at least {check.count} of this: {phrase}"
    if kind == "between":
        return f"must have {_range(check)} for “{_words(check.pattern.predicate)}”"
    if kind == "implies":
        assert check.if_has is not None
        return f"if it has this: {_phrase(check.if_has, names)}, must also have this: {phrase}"
    if kind == "not_both":
        assert check.not_with is not None
        return f"cannot have both this: {phrase} and this: {_phrase(check.not_with, names)}"
    subject = (
        names.get(check.about_id, "an entry that is no longer there") if check.about_id else "it"
    )
    believed = _phrase(check.pattern, names, subject)
    if kind == "believes":
        return f"must believe this: {believed}"
    return f"must not believe this: {believed}"


def read_rule(body: RuleBody, names: dict[str, str]) -> str:
    """The rule in ordinary words, built from the rule itself so it cannot drift from it."""
    selector = body.applies_to
    if selector.entity_id is not None:
        about = names.get(selector.entity_id, "An entry that is no longer there")
    else:
        about = f"Every {_words(selector.type)}" if selector.type else "Every entry"
        if selector.tag:
            about += f" tagged “{selector.tag}”"
    if selector.having:
        about += " where " + " and ".join(_phrase(item, names) for item in selector.having) + ","
    exceptions = body.except_for
    if not exceptions.empty():
        excused = [
            names.get(item, "an entry that is no longer there") for item in exceptions.entity_ids
        ]
        if exceptions.tag:
            excused.append(f"anything tagged “{exceptions.tag}”")
        excused.extend(f"anything where {_phrase(item, names)}" for item in exceptions.having)
        about += ("" if selector.having else ",") + f" except for {_listed(excused)},"
    parts = [_read_check(check, names) for check in body.checks]
    joiner = "; and " if body.mode == "all" else "; or "
    when = {
        "as_recorded": "",
        "every_moment": "At every moment of the story: ",
        "at_end": "At the end of the story: ",
    }[body.time]
    return f"{when}{about} {joiner.join(parts)}."


def referenced_ids(body: RuleBody) -> set[str]:
    found = {body.applies_to.entity_id} if body.applies_to.entity_id else set()
    found.update(body.except_for.entity_ids)
    for pattern in _all_patterns(body):
        if pattern.other_id:
            found.add(pattern.other_id)
    for check in body.checks:
        if check.about_id:
            found.add(check.about_id)
    return found


def _all_patterns(body: RuleBody) -> list[Pattern]:
    found = [*body.applies_to.having, *body.except_for.having]
    for check in body.checks:
        found.append(check.pattern)
        found.extend(item for item in (check.if_has, check.not_with) if item is not None)
    return found


def body_problems(
    body: RuleBody, *, predicates: set[str], entity_types: set[str], entity_ids: set[str]
) -> list[str]:
    """Why a rule cannot be kept as written, in an author's words. Empty when it can."""
    problems: list[str] = []
    selector = body.applies_to
    if selector.type is not None and selector.type not in entity_types:
        problems.append(f"There is no type of entry called {selector.type}.")
    if selector.entity_id is not None and selector.entity_id not in entity_ids:
        problems.append("The entry this rule is about is not in the story bible.")
    if any(item not in entity_ids for item in body.except_for.entity_ids):
        problems.append("An entry this rule leaves out is not in the story bible.")
    for pattern in _all_patterns(body):
        if pattern.predicate not in predicates:
            problems.append(f"There is no kind of fact called “{_words(pattern.predicate)}”.")
        if pattern.other_type is not None and pattern.other_type not in entity_types:
            problems.append(f"There is no type of entry called {pattern.other_type}.")
        if pattern.other_id is not None and pattern.other_id not in entity_ids:
            problems.append("An entry this rule names is not in the story bible.")
    for check in body.checks:
        if check.about_id is not None and check.about_id not in entity_ids:
            problems.append("The entry a belief in this rule is about is not in the story bible.")
    return list(dict.fromkeys(problems))
