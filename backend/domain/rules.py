"""Story rules: an author's own continuity rule, as data that can be checked (ADR-013).

A rule says which entries it is about and what must hold for each of them. It is
a small fixed vocabulary, never code: nothing an author or a model supplies is
executed. Evaluation is deterministic over entries and the facts that are
currently true, so the same story always gives the same answer.

A rule carries examples: tiny made-up stories it should catch and ones it should
allow. They run through the same evaluator as the real story, so what a rule is
shown to do and what it does cannot differ.

In this phase a rule only ever warns. Time is not modelled yet (Phase 3), so a
rule speaks about the story as it stands, not about before and after.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.canon.assertions import Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.marks import TAGS_KEY, clean_tags
from backend.domain.canon.naming import normalize_name
from backend.domain.operations import (
    AssertFactOp,
    CanonOperation,
    CloseFactOp,
    CreateEntityOp,
    RetireEntityOp,
    UpdateEntityOp,
)
from backend.domain.validation import RuleEnforcement

MAX_RULES = 100
MAX_TITLE_LENGTH = 160
MAX_SENTENCE_LENGTH = 600
MAX_PATTERNS = 4
MAX_CHECKS = 4
MAX_EXAMPLES = 8
MAX_EXAMPLE_ENTRIES = 12
MAX_EXAMPLE_FACTS = 24
MAX_COUNT = 1000
MAX_VIOLATIONS = 200


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
    model_config = ConfigDict(extra="forbid")

    kind: Literal["has", "lacks", "at_most", "at_least"]
    pattern: Pattern
    count: int = Field(default=1, ge=0, le=MAX_COUNT, description="For at_most and at_least")


class Selector(BaseModel):
    """Which entries a rule is about. Every part given must match."""

    model_config = ConfigDict(extra="forbid")

    type: str | None = Field(default=None, max_length=100)
    tag: str | None = Field(default=None, max_length=60)
    entity_id: str | None = Field(default=None, max_length=200)
    having: list[Pattern] = Field(default_factory=list, max_length=MAX_PATTERNS)


class RuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applies_to: Selector = Field(default_factory=Selector)
    # all: every check must hold. any: one is enough.
    mode: Literal["all", "any"] = "all"
    checks: list[Check] = Field(min_length=1, max_length=MAX_CHECKS)


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


class RuleExample(BaseModel):
    """A tiny made-up story, and whether the rule should catch it."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=300)
    entries: list[ExampleEntry] = Field(default_factory=list, max_length=MAX_EXAMPLE_ENTRIES)
    facts: list[ExampleFact] = Field(default_factory=list, max_length=MAX_EXAMPLE_FACTS)
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


@dataclass
class World:
    """Entries and the facts currently true of them. All a rule can see."""

    entries: dict[str, WorldEntry] = field(default_factory=dict)
    facts: dict[str, WorldFact] = field(default_factory=dict)

    def copy(self) -> World:
        return World(entries=dict(self.entries), facts=dict(self.facts))


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


def world_from_canon(entities: Iterable[CanonEntity], facts: Iterable[Fact]) -> World:
    world = World()
    for entity in entities:
        if entity.is_active:
            world.entries[entity.id] = WorldEntry(
                entity.id, entity.type, entity.name, _tags(entity.attributes)
            )
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
        )
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
                    current.type,
                    operation.name or current.name,
                    current.tags if operation.attributes is None else _tags(operation.attributes),
                )
        elif isinstance(operation, RetireEntityOp):
            after.entries.pop(operation.id, None)
        elif isinstance(operation, AssertFactOp):
            obj = operation.proposition.object
            after.facts[operation.id] = WorldFact(
                id=operation.id,
                subject_id=operation.proposition.subject_id,
                predicate=operation.proposition.predicate,
                object_id=getattr(obj, "entity_id", None),
                value=_scalar(obj),
            )
        elif isinstance(operation, CloseFactOp):
            after.facts.pop(operation.id, None)
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
        )
    return world


# ----------------------------------------------------------------- evaluation
@dataclass(frozen=True)
class Violation:
    rule_id: str
    entity_id: str
    entity_name: str


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
        for fact in world.facts.values():
            self.outgoing.setdefault((fact.subject_id, fact.predicate), []).append(fact)
            self.touching.setdefault(fact.subject_id, []).append(fact)
            if fact.object_id is not None:
                self.incoming.setdefault((fact.object_id, fact.predicate), []).append(fact)
                if fact.object_id != fact.subject_id:
                    self.touching.setdefault(fact.object_id, []).append(fact)


def _matches(world: World, index: FactIndex, entry_id: str, pattern: Pattern) -> int:
    """How many facts of this shape the entry has."""
    found = 0
    key = (entry_id, pattern.predicate)
    outgoing = pattern.direction == "out"
    for fact in (index.outgoing if outgoing else index.incoming).get(key, ()):
        other_id = fact.object_id if outgoing else fact.subject_id
        # A fact about an entry that is gone does not count.
        other = world.entries.get(other_id) if other_id is not None else None
        if other_id is not None and other is None:
            continue
        if pattern.other_id is not None and other_id != pattern.other_id:
            continue
        if pattern.other_type is not None and (other is None or other.type != pattern.other_type):
            continue
        if pattern.value is not None and (
            fact.value is None or normalize_name(fact.value) != normalize_name(pattern.value)
        ):
            continue
        found += 1
    return found


def _selected(
    world: World, index: FactIndex, selector: Selector, only: Iterable[str] | None = None
) -> list[WorldEntry]:
    candidates: Iterable[WorldEntry] = (
        world.entries.values()
        if only is None
        else [world.entries[item] for item in set(only) if item in world.entries]
    )
    chosen: list[WorldEntry] = []
    for entry in candidates:
        if selector.entity_id is not None and entry.id != selector.entity_id:
            continue
        if selector.type is not None and entry.type != selector.type:
            continue
        if selector.tag is not None and normalize_name(selector.tag) not in entry.tags:
            continue
        if any(_matches(world, index, entry.id, pattern) == 0 for pattern in selector.having):
            continue
        chosen.append(entry)
    return sorted(chosen, key=lambda item: (item.name.casefold(), item.id))


def selected_entries(
    body: RuleBody, world: World, only: Iterable[str], *, index: FactIndex | None = None
) -> list[WorldEntry]:
    """Which of ``only`` the rule is about in this world, whether or not it holds for them."""
    return _selected(world, index if index is not None else FactIndex(world), body.applies_to, only)


def _holds(world: World, index: FactIndex, entry_id: str, check: Check) -> bool:
    count = _matches(world, index, entry_id, check.pattern)
    if check.kind == "has":
        return count > 0
    if check.kind == "lacks":
        return count == 0
    if check.kind == "at_most":
        return count <= check.count
    return count >= check.count


def violations(
    rule_id: str,
    body: RuleBody,
    world: World,
    *,
    limit: int | None = MAX_VIOLATIONS,
    index: FactIndex | None = None,
    only: Iterable[str] | None = None,
) -> list[Violation]:
    """Every entry the rule is about for which it does not hold.

    ``limit`` caps how many are returned, for showing; pass None to get all of them,
    as deciding whether a change newly breaks a rule must. ``only`` restricts the
    answer to those entries.
    """
    index = index if index is not None else FactIndex(world)
    found: list[Violation] = []
    for entry in _selected(world, index, body.applies_to, only):
        results = [_holds(world, index, entry.id, check) for check in body.checks]
        ok = all(results) if body.mode == "all" else any(results)
        if not ok:
            found.append(Violation(rule_id, entry.id, entry.name))
            if limit is not None and len(found) >= limit:
                break
    return found


_WORLD_CHANGES = (CreateEntityOp, UpdateEntityOp, RetireEntityOp, AssertFactOp, CloseFactOp)


def changes_world(operations: Sequence[CanonOperation]) -> bool:
    """Whether any of these changes alters what a rule can see."""
    return any(isinstance(operation, _WORLD_CHANGES) for operation in operations)


def affected_entries(
    before: World,
    after: World,
    operations: Sequence[CanonOperation],
    *,
    indexes: tuple[FactIndex, FactIndex] | None = None,
) -> set[str]:
    """Every entry whose standing under any rule these changes could alter.

    An entry's standing depends only on the entry itself, the facts about or
    pointing at it, and the entries at the other end of those facts. So it can
    change only for an entry a change names, an end of a fact added or ended, or a
    neighbour of an entry that was added, changed or removed.
    """
    before_index, after_index = indexes or (FactIndex(before), FactIndex(after))
    affected: set[str] = set()

    def ends(fact: WorldFact | None) -> None:
        if fact is not None:
            affected.add(fact.subject_id)
            if fact.object_id is not None:
                affected.add(fact.object_id)

    for operation in operations:
        if isinstance(operation, (CreateEntityOp, UpdateEntityOp, RetireEntityOp)):
            affected.add(operation.id)
            for index in (before_index, after_index):
                for fact in index.touching.get(operation.id, ()):
                    ends(fact)
        elif isinstance(operation, (AssertFactOp, CloseFactOp)):
            ends(before.facts.get(operation.id))
            ends(after.facts.get(operation.id))
            proposition = getattr(operation, "proposition", None)
            if proposition is not None:
                affected.add(proposition.subject_id)
                other = getattr(proposition.object, "entity_id", None)
                if other is not None:
                    affected.add(other)
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


def _phrase(pattern: Pattern, names: dict[str, str]) -> str:
    other = _other(pattern, names)
    if pattern.direction == "out":
        return f"it {_verb(pattern.predicate)} {other}"
    return f"{other} {_verb(pattern.predicate)} it"


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
    parts: list[str] = []
    for check in body.checks:
        phrase = _phrase(check.pattern, names)
        if check.kind == "has":
            parts.append(f"must have this: {phrase}")
        elif check.kind == "lacks":
            parts.append(f"must not have this: {phrase}")
        elif check.kind == "at_most":
            parts.append(f"can have at most {check.count} of this: {phrase}")
        else:
            parts.append(f"needs at least {check.count} of this: {phrase}")
    joiner = "; and " if body.mode == "all" else "; or "
    return f"{about} {joiner.join(parts)}."


def referenced_ids(body: RuleBody) -> set[str]:
    found = {body.applies_to.entity_id} if body.applies_to.entity_id else set()
    for pattern in [*body.applies_to.having, *(check.pattern for check in body.checks)]:
        if pattern.other_id:
            found.add(pattern.other_id)
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
    for pattern in [*selector.having, *(check.pattern for check in body.checks)]:
        if pattern.predicate not in predicates:
            problems.append(f"There is no kind of fact called “{_words(pattern.predicate)}”.")
        if pattern.other_type is not None and pattern.other_type not in entity_types:
            problems.append(f"There is no type of entry called {pattern.other_type}.")
        if pattern.other_id is not None and pattern.other_id not in entity_ids:
            problems.append("An entry this rule names is not in the story bible.")
    return list(dict.fromkeys(problems))
