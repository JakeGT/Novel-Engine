"""What a change to the story touches: the dependency index, read on demand (CON-007, CON-017).

Before a change is approved, the author sees what depends on what it changes. The
answer is worked out from data the story already keeps, the same way every time,
with no model:

- the plan: scenes and other parts whose people and places, conditions or reveals
  name a changed entry or fact, and threads that are about it or that a changed part
  of the plan opens, moves along or resolves;
- story rules that are about a changed entry, before or after the change;
- kept drafts whose recorded context included a changed entry, fact or part of the
  plan (the context manifest lists every record it gave);
- the author's writing, through the passage index, which keeps the entries each
  passage names (never the words).

Each dependent is classified. *Affected* means the story's own records name the
changed thing. *May be affected* means writing mentions it by name, which is
evidence, not proof. Everything the index does not cover is listed as *not checked*,
so an incomplete answer is never presented as the whole (plan/13, "Incremental
invalidation"). This is the same split a refactoring preview makes between code
references and matches in comments and text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from backend.domain.canon.phrasing import phrase
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.knowledge import scene_reveals
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
    CreatePlanItemOp,
    LinkAssetOp,
    RetireEntityOp,
    RetireEventOp,
    RetirePlanItemOp,
    UpdateEntityOp,
    UpdatePlanItemOp,
)
from backend.domain.rules import (
    FactIndex,
    RuleStatus,
    StoryRule,
    World,
    WorldFact,
    referenced_ids,
    selected_entries,
)
from backend.domain.scene_conditions import condition_reading, scene_conditions
from backend.domain.threads import thread_advances
from backend.domain.validation import RuleEnforcement

Level = Literal["affected", "may_be_affected"]
Kind = Literal["scene", "plan", "thread", "rule", "draft", "scene_text", "text"]

# Enough to act on; past this the answer says it stopped.
MAX_DEPENDENTS = 200

_ROLE = {
    "point_of_view": "{name} is the point of view",
    "place": "{name} is where it happens",
    "participant": "{name} is in it",
    "about": "It is about {name}",
}
_STEP = {"opens": "It opens", "advances": "It moves along", "resolves": "It resolves"}


@dataclass
class Touched:
    """What a change names: entries, facts and parts of the plan, each with how it changes."""

    entries: dict[str, str] = field(default_factory=dict)
    facts: dict[str, str] = field(default_factory=dict)
    plans: dict[str, str] = field(default_factory=dict)
    # Threads a new or changed part of the plan would open, move along or resolve, and why.
    threads: dict[str, list[str]] = field(default_factory=dict)
    # What the change does, in the author's words: "Mara is changed".
    about: list[str] = field(default_factory=list)
    # New entries are not in the index yet.
    new_entries: int = 0

    def entry(self, entry_id: str, name: str) -> None:
        self.entries.setdefault(entry_id, name)

    def say(self, words: str) -> None:
        if words not in self.about:
            self.about.append(words)

    @property
    def ids(self) -> set[str]:
        return set(self.entries) | set(self.facts) | set(self.plans)


@dataclass(frozen=True)
class StoryFacts:
    """The story as the dependency index reads it: names and the ends of every fact."""

    names: Mapping[str, str]
    facts: Mapping[str, WorldFact]
    # belief id -> (holder, subject)
    beliefs: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    # event id -> the entries it involves
    events: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    plans: Mapping[str, PlanItem] = field(default_factory=dict)

    def name(self, entry_id: str) -> str:
        return self.names.get(entry_id, "an entry")

    def reading(self, fact: WorldFact) -> str:
        other = (
            self.name(fact.object_id) if fact.object_id is not None else (fact.value or "something")
        )
        return f"{self.name(fact.subject_id)} {phrase(fact.predicate)} {other}"

    def ends(self, fact_id: str) -> set[str]:
        fact = self.facts.get(fact_id)
        if fact is None:
            return set()
        return {fact.subject_id} | ({fact.object_id} if fact.object_id else set())


@dataclass
class Dependent:
    kind: Kind
    level: Level
    id: str
    title: str
    reasons: list[str] = field(default_factory=list)
    # Where to open it: a scene in the studio or plan, a text at a passage.
    plan_id: str | None = None
    source_id: str | None = None
    version_id: str | None = None
    start: int | None = None
    end: int | None = None
    # How many passages mention it, for writing.
    count: int = 0


@dataclass(frozen=True)
class Mention:
    """One passage the index says names an entry, in a text usable in this line of the story."""

    source_id: str
    title: str
    version_id: str
    start: int
    end: int
    entity_ids: tuple[str, ...]
    # The scene this text is the prose of, if it is one.
    plan_id: str | None = None


@dataclass(frozen=True)
class KeptDraft:
    """A kept version of a scene's prose and the records its context gave."""

    plan_id: str
    title: str
    version_id: str
    version_number: int
    records: frozenset[str]


@dataclass
class Impact:
    about: list[str]
    affected: list[Dependent]
    may_be_affected: list[Dependent]
    not_checked: list[str]


# ---------------------------------------------------------------- what a change names


def touched_by(operations: Sequence[CanonOperation], story: StoryFacts) -> Touched:
    """Everything these changes name, read against the story as it stands."""
    touched = Touched()
    new_names: dict[str, str] = {}
    for operation in operations:
        if isinstance(operation, CreateEntityOp):
            new_names[operation.id] = operation.name
            touched.new_entries += 1
            touched.say(f"{operation.name} is added")

    def name(entry_id: str) -> str:
        return new_names.get(entry_id) or story.name(entry_id)

    def existing(entry_id: str) -> None:
        if entry_id in story.names:
            touched.entry(entry_id, story.name(entry_id))

    for operation in operations:
        if isinstance(operation, UpdateEntityOp):
            existing(operation.id)
            touched.say(f"{name(operation.id)} is changed")
        elif isinstance(operation, RetireEntityOp):
            existing(operation.id)
            touched.say(f"{name(operation.id)} is archived")
        elif isinstance(operation, AssertFactOp):
            proposition = operation.proposition
            other_id = getattr(proposition.object, "entity_id", None)
            for entry_id in (proposition.subject_id, other_id):
                if entry_id:
                    existing(entry_id)
            value = getattr(proposition.object, "value", None)
            other = name(other_id) if other_id else (str(value) if value is not None else "")
            words = f"{name(proposition.subject_id)} {phrase(proposition.predicate)} {other}"
            touched.say(f"New fact: {words.strip()}")
        elif isinstance(operation, (CloseFactOp, AmendFactOp)):
            fact = story.facts.get(operation.id)
            if fact is None:
                continue
            reading = story.reading(fact)
            touched.facts[fact.id] = reading
            for entry_id in story.ends(fact.id):
                existing(entry_id)
            ended = isinstance(operation, CloseFactOp)
            touched.say(
                f"No longer true: {reading}" if ended else f"When or who knows changes: {reading}"
            )
        elif isinstance(operation, AssertBeliefOp):
            for entry_id in (operation.holder_entity_id, operation.proposition.subject_id):
                existing(entry_id)
            touched.say(f"{name(operation.holder_entity_id)} comes to believe something")
        elif isinstance(operation, CloseBeliefOp):
            belief = story.beliefs.get(operation.id)
            if belief is not None:
                for entry_id in belief:
                    existing(entry_id)
                touched.say(f"{name(belief[0])} stops believing something")
        elif isinstance(operation, CreateEventOp):
            involved = [item.entity_id for item in operation.participants]
            for entry_id in [*involved, *operation.location_ids]:
                existing(entry_id)
            touched.say(f"New event: {operation.name}")
        elif isinstance(operation, (AmendEventOp, RetireEventOp)):
            for entry_id in story.events.get(operation.id, ()):
                existing(entry_id)
            touched.say(
                "An event changes" if isinstance(operation, AmendEventOp) else "An event is removed"
            )
        elif isinstance(operation, LinkAssetOp):
            if operation.target_record_type == "entity":
                existing(operation.target_id)
                touched.say(f"A picture is linked to {name(operation.target_id)}")
        elif isinstance(operation, (UpdatePlanItemOp, RetirePlanItemOp)):
            item = story.plans.get(operation.id)
            if item is None:
                continue
            touched.plans[item.id] = item.title
            touched.say(
                f"“{item.title}” in the plan is "
                + ("removed" if isinstance(operation, RetirePlanItemOp) else "changed")
            )
            if isinstance(operation, UpdatePlanItemOp):
                _threads_named(operation, item.title, "changes", story, touched)
        elif isinstance(operation, CreatePlanItemOp):
            touched.say(f"“{operation.title}” is added to the plan")
            _threads_named(operation, operation.title, "is added", story, touched)
    return touched


def _threads_named(
    operation: CreatePlanItemOp | UpdatePlanItemOp,
    title: str,
    how: str,
    story: StoryFacts,
    touched: Touched,
) -> None:
    """A thread a new or changed part of the plan would open, move along or resolve."""
    for step, ids in (
        ("opens", operation.sets_up or []),
        ("moves along", thread_advances(operation.details or {})),
        ("resolves", operation.pays_off or []),
    ):
        for thread_id in ids:
            thread = story.plans.get(thread_id)
            if thread is None or thread.kind is not PlanKind.THREAD:
                continue
            reasons = touched.threads.setdefault(thread.id, [])
            reason = f"“{title}”, which {how}, {step} it"
            if reason not in reasons:
                reasons.append(reason)


def touched_entry(entry_id: str, story: StoryFacts) -> Touched:
    """Everything that depends on one entry, as if it were about to change."""
    touched = Touched()
    touched.entry(entry_id, story.name(entry_id))
    touched.say(f"{story.name(entry_id)} is changed")
    return touched


# ----------------------------------------------------------------- what depends on it


def plan_dependents(touched: Touched, story: StoryFacts) -> list[Dependent]:
    """Scenes, other parts of the plan, and threads whose records name what changes."""
    found: list[Dependent] = []
    entries = touched.entries
    names = dict(story.names)
    for item in story.plans.values():
        if not item.is_active or item.id in touched.plans:
            continue  # inactive, or itself what changes
        reasons: list[str] = []
        for ref in item.refs:
            if ref.entity_id in entries:
                template = _ROLE.get(ref.role, "{name} is part of it")
                reasons.append(template.format(name=entries[ref.entity_id]))
        for condition in scene_conditions(item.details):
            others = {condition.entity_id} if condition.entity_id else set()
            if condition.check is not None and condition.check.pattern.other_id:
                others.add(condition.check.pattern.other_id)
            if others & set(entries):
                when = "before" if condition.when == "before" else "after"
                reading = condition_reading(condition, names)
                reasons.append(f"A condition {when} it: “{reading}”")
        for reveal in scene_reveals(item.details):
            direct = reveal.fact_id in touched.facts
            about = story.ends(reveal.fact_id) & set(entries)
            if not (direct or about):
                continue
            fact = story.facts.get(reveal.fact_id)
            reading = story.reading(fact) if fact is not None else "a fact"
            verb = "shows the reader" if reveal.how == "shown" else "hints to the reader"
            reasons.append(f"It {verb}: {reading}")
        advancing = thread_advances(item.details)
        for thread_id, title in touched.plans.items():
            if thread_id == item.id:
                continue
            for step, ids in (
                ("opens", item.sets_up),
                ("advances", advancing),
                ("resolves", item.pays_off),
            ):
                if thread_id in ids:
                    reasons.append(f"{_STEP[step]} “{title}”")
        if item.kind is PlanKind.THREAD:
            # A thread is also touched when a part of the plan that carries it changes.
            reasons.extend(touched.threads.get(item.id, []))
            for other_id, title in touched.plans.items():
                other = story.plans.get(other_id)
                if other is None:
                    continue
                steps = [
                    word
                    for word, ids in (
                        ("opens", other.sets_up),
                        ("moves along", thread_advances(other.details)),
                        ("resolves", other.pays_off),
                    )
                    if item.id in ids
                ]
                if steps:
                    reasons.append(f"“{title}”, which changes, {' and '.join(steps)} it")
        if not reasons:
            continue
        kind: Kind = (
            "scene"
            if item.kind is PlanKind.SCENE
            else "thread"
            if item.kind is PlanKind.THREAD
            else "plan"
        )
        found.append(
            Dependent(
                kind=kind,
                level="affected",
                id=item.id,
                title=item.title,
                reasons=_unique(reasons),
                plan_id=item.id,
            )
        )
    return found


def rule_dependents(
    touched: Touched, rules: Iterable[StoryRule], before: World, after: World
) -> list[Dependent]:
    """Story rules about a changed entry, before or after the change."""
    ids = set(touched.entries)
    if not ids:
        return []
    indexes = (FactIndex(before), FactIndex(after))
    found: list[Dependent] = []
    for rule in rules:
        reasons: list[str] = []
        about = {
            entry.id
            for world, index in zip((before, after), indexes, strict=True)
            for entry in selected_entries(rule.body, world, ids, index=index)
        }
        for entry_id in sorted(about, key=lambda value: touched.entries[value]):
            reasons.append(f"It applies to {touched.entries[entry_id]}")
        for entry_id in sorted(referenced_ids(rule.body) & ids - about):
            reasons.append(f"It names {touched.entries[entry_id]}")
        if not reasons:
            continue
        if rule.status is RuleStatus.PAUSED:
            reasons.append("It is paused, so it is not checked now")
        elif rule.enforcement is RuleEnforcement.BLOCKING:
            reasons.append("It stops a change that breaks it")
        found.append(
            Dependent(kind="rule", level="affected", id=rule.id, title=rule.title, reasons=reasons)
        )
    return found


def draft_dependents(
    touched: Touched, drafts: Iterable[KeptDraft], story: StoryFacts
) -> list[Dependent]:
    """Kept drafts whose context included what changes, one entry per scene."""
    by_scene: dict[str, Dependent] = {}
    for draft in sorted(drafts, key=lambda item: (item.plan_id, -item.version_number)):
        given: list[str] = []
        for record_id in sorted(draft.records):
            if record_id in touched.entries:
                given.append(touched.entries[record_id])
            elif record_id in touched.facts:
                given.append(touched.facts[record_id])
            elif record_id in touched.plans:
                given.append(f"“{touched.plans[record_id]}”")
            elif story.ends(record_id) & set(touched.entries):
                fact = story.facts[record_id]
                given.append(story.reading(fact))
        if not given:
            continue
        shown = ", ".join(_unique(given)[:3]) + (" and more" if len(_unique(given)) > 3 else "")
        reason = f"Version {draft.version_number} was drafted with {shown} in what was given"
        current = by_scene.get(draft.plan_id)
        if current is None:
            by_scene[draft.plan_id] = Dependent(
                kind="draft",
                level="affected",
                id=draft.plan_id,
                title=draft.title,
                reasons=[reason],
                plan_id=draft.plan_id,
                version_id=draft.version_id,
            )
        else:
            current.reasons.append(reason)
    return list(by_scene.values())


def own_text_dependents(
    touched: Touched, manuscripts: Mapping[str, tuple[str, str]]
) -> list[Dependent]:
    """The written text of a scene whose plan changes: written to the plan as it was.

    ``manuscripts`` is plan id -> (source id, the text's title) in this line of the story.
    """
    found: list[Dependent] = []
    for plan_id, title in touched.plans.items():
        if plan_id not in manuscripts:
            continue
        source_id, _ = manuscripts[plan_id]
        found.append(
            Dependent(
                kind="scene_text",
                level="may_be_affected",
                id=source_id,
                title=title,
                reasons=["It was written to the scene’s plan as it was before this change"],
                plan_id=plan_id,
                source_id=source_id,
            )
        )
    return found


def text_dependents(touched: Touched, mentions: Iterable[Mention]) -> list[Dependent]:
    """Writing whose passages name a changed entry. Named is not the same as depends on."""
    ids = set(touched.entries)
    found: dict[str, Dependent] = {}
    named: dict[str, list[str]] = {}
    for passage in mentions:
        shared = [entry_id for entry_id in passage.entity_ids if entry_id in ids]
        if not shared:
            continue
        current = found.get(passage.source_id)
        if current is None:
            current = found[passage.source_id] = Dependent(
                kind="scene_text" if passage.plan_id else "text",
                level="may_be_affected",
                id=passage.source_id,
                title=passage.title,
                plan_id=passage.plan_id,
                source_id=passage.source_id,
                version_id=passage.version_id,
                start=passage.start,
                end=passage.end,
            )
            named[passage.source_id] = []
        current.count += 1
        for entry_id in shared:
            if touched.entries[entry_id] not in named[passage.source_id]:
                named[passage.source_id].append(touched.entries[entry_id])
    for source_id, dependent in found.items():
        who = _and(named[source_id])
        where = "1 passage" if dependent.count == 1 else f"{dependent.count} passages"
        dependent.reasons = [f"Mentions {who} by name in {where}"]
    return list(found.values())


# ------------------------------------------------------------------------ the answer


def assemble(
    touched: Touched,
    *,
    plan: Sequence[Dependent],
    rules: Sequence[Dependent],
    drafts: Sequence[Dependent],
    texts: Sequence[Dependent],
    not_checked: Sequence[str],
) -> Impact:
    order = {"scene": 0, "plan": 1, "thread": 2, "rule": 3, "draft": 4, "scene_text": 5, "text": 6}
    affected = sorted([*plan, *rules, *drafts], key=lambda d: (order[d.kind], d.title.casefold()))
    merged: dict[tuple[str, str], Dependent] = {}
    for dependent in texts:
        same = merged.get((dependent.kind, dependent.id))
        if same is None:
            merged[(dependent.kind, dependent.id)] = dependent
            continue
        same.reasons = _unique([*same.reasons, *dependent.reasons])
        same.count = max(same.count, dependent.count)
        if same.start is None:
            same.version_id, same.start, same.end = (
                dependent.version_id,
                dependent.start,
                dependent.end,
            )
    may_be = sorted(merged.values(), key=lambda d: (order[d.kind], -d.count, d.title.casefold()))
    gaps = list(not_checked)
    if len(affected) > MAX_DEPENDENTS or len(may_be) > MAX_DEPENDENTS:
        gaps.append(
            f"Only the first {MAX_DEPENDENTS} of each are listed; there are more than that."
        )
    return Impact(
        about=list(touched.about),
        affected=affected[:MAX_DEPENDENTS],
        may_be_affected=may_be[:MAX_DEPENDENTS],
        not_checked=gaps,
    )


def standing_gaps(touched: Touched) -> list[str]:
    """What the index never covers, said every time so the answer is not taken as whole."""
    gaps: list[str] = []
    if touched.entries:
        gaps.append(
            f"Writing that refers to {_and(sorted(touched.entries.values()))} without a name "
            "or alias the story bible knows, such as “she” or “the captain”."
        )
    if touched.new_entries:
        gaps.append("Writing is not searched for new entries until they are added.")
    gaps.append(
        "Checks and notes in the scene studio. They are worked out again the next time a "
        "scene is checked."
    )
    gaps.append("What characters believe or know about it, and events it is part of.")
    return gaps


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _and(names: Sequence[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    if len(names) > 3:
        return f"{', '.join(names[:3])} and others"
    return f"{', '.join(names[:-1])} and {names[-1]}"


__all__ = [
    "Dependent",
    "Impact",
    "KeptDraft",
    "Mention",
    "StoryFacts",
    "Touched",
    "assemble",
    "draft_dependents",
    "own_text_dependents",
    "plan_dependents",
    "rule_dependents",
    "standing_gaps",
    "text_dependents",
    "touched_by",
    "touched_entry",
]
