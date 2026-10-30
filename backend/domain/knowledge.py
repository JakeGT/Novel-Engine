"""Who knows what, and what the reader has been shown (ADR-020).

Knowledge is deny-by-default. A character knows a fact when they hold a belief
that agrees with it, or when the fact is something everyone in the story knows.
A secret is known only to those who hold it. Whether a belief is right is
derived from the facts each time, never stored; so is whether something one
character told another was true.

The reader is a separate ledger. Each planned scene can say which facts it
shows the reader, plainly or as a hint, and scenes are read in the order of the
plan. From the two ledgers come dramatic irony (the reader knows what the
scene's viewpoint character does not), withheld knowledge (the reader has not
been shown what the viewpoint character knows) and reveals repeated later.

What is *said* is a third thing (KNW-005): a claim is an event of kind ``claim`` that
records what the text, a character or a narrator says, how, and who could perceive it.
It is never a fact and never counts as knowledge. Whether the facts bear it out is
derived each time, like whether a belief is right.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.domain.canon.assertions import Belief, Fact, FactStatus, FactVisibility
from backend.domain.canon.events import (
    CLAIM_KIND,
    CLAIM_NARRATOR,
    CLAIM_PERCEIVER,
    CLAIM_SPEAKER,
    Event,
    EventClaim,
)
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.canon.propositions import EntityRefObject
from backend.domain.worldtime import (
    WORLD_TIME,
    At,
    Between,
    Interval,
    Relative,
    Unknown,
    parse_world_time,
)

# Kept in a scene's details under reserved keys, like its conditions.
REVEALS_KEY = "_reveals"
SCENE_TIME_KEY = "_when"
MAX_REVEALS = 30


class Reveal(BaseModel):
    """A fact a scene shows the reader: plainly, or only as a hint."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1, max_length=200)
    how: Literal["shown", "hinted"] = "shown"
    # The scene where it is meant to come out (STY-007). Showing it plainly in an
    # earlier scene is then too early; hints before it are clues.
    main_reveal: bool = False

    @model_validator(mode="after")
    def _shown_when_it_comes_out(self) -> Reveal:
        if self.main_reveal and self.how != "shown":
            raise ValueError("where a fact comes out, it is shown plainly, not hinted")
        return self


def scene_reveals(details: dict[str, str]) -> list[Reveal]:
    raw = details.get(REVEALS_KEY)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    found: list[Reveal] = []
    for item in items if isinstance(items, list) else []:
        try:
            found.append(Reveal.model_validate(item))
        except ValidationError:
            continue
    return found[:MAX_REVEALS]


def with_reveals(details: dict[str, str], reveals: Sequence[Reveal]) -> dict[str, str]:
    kept = {key: value for key, value in details.items() if key != REVEALS_KEY}
    if reveals:
        # Stored as before unless it is where the fact comes out, so an unchanged
        # list still reads as unchanged.
        kept[REVEALS_KEY] = json.dumps(
            [
                item.model_dump(exclude={"main_reveal"} if not item.main_reveal else None)
                for item in reveals
            ],
            separators=(",", ":"),
        )
    return kept


def scene_time(details: dict[str, str]) -> Unknown | At | Between | Relative | None:
    raw = details.get(SCENE_TIME_KEY)
    if not raw:
        return None
    try:
        return parse_world_time(json.loads(raw))
    except ValueError:
        return None


def with_scene_time(details: dict[str, str], when: object | None) -> dict[str, str]:
    kept = {key: value for key, value in details.items() if key != SCENE_TIME_KEY}
    if when is not None and not (isinstance(when, Unknown) and not when.label):
        kept[SCENE_TIME_KEY] = WORLD_TIME.dump_json(when).decode()  # type: ignore[arg-type]
    return kept


# --------------------------------------------------------------- characters


def agrees(belief: Belief, fact: Fact) -> bool:
    return (
        belief.proposition.subject_id == fact.proposition.subject_id
        and belief.proposition.predicate == fact.proposition.predicate
        and belief.proposition.object == fact.proposition.object
    )


def _covers(belief: Belief, fact: Fact) -> bool:
    return (
        belief.proposition.subject_id == fact.proposition.subject_id
        and belief.proposition.predicate == fact.proposition.predicate
    )


def _current(facts: Iterable[Fact]) -> list[Fact]:
    return [
        fact
        for fact in facts
        if fact.status is FactStatus.ASSERTED and fact.closed_revision is None
    ]


@dataclass
class CharacterKnowledge:
    holder_id: str
    # Facts the character knows, and how: through a belief or because everyone knows.
    knows: list[tuple[Fact, Literal["believes", "everyone"]]] = field(default_factory=list)
    # Beliefs the facts contradict, each with the fact it gets wrong.
    wrong: list[tuple[Belief, Fact]] = field(default_factory=list)
    # Beliefs nothing in the story confirms or contradicts.
    unconfirmed: list[Belief] = field(default_factory=list)
    # Secrets the character does not know.
    unaware: list[Fact] = field(default_factory=list)
    # Ordinary facts nothing says the character knows (deny-by-default).
    unrecorded: int = 0

    def known_ids(self) -> set[str]:
        return {fact.id for fact, _ in self.knows}


def knowledge_of(
    holder_id: str, facts: Iterable[Fact], beliefs: Iterable[Belief]
) -> CharacterKnowledge:
    current = _current(facts)
    held = [
        belief
        for belief in beliefs
        if belief.holder_entity_id == holder_id and belief.closed_revision is None
    ]
    result = CharacterKnowledge(holder_id=holder_id)
    for fact in current:
        if any(agrees(belief, fact) for belief in held):
            result.knows.append((fact, "believes"))
        elif fact.visibility is FactVisibility.COMMON:
            result.knows.append((fact, "everyone"))
        elif fact.visibility is FactVisibility.SECRET:
            result.unaware.append(fact)
        else:
            result.unrecorded += 1
    for belief in held:
        covering = [fact for fact in current if _covers(belief, fact)]
        if not covering:
            result.unconfirmed.append(belief)
        elif not any(agrees(belief, fact) for fact in covering):
            result.wrong.append((belief, covering[0]))
    return result


@dataclass
class FactKnowledge:
    fact: Fact
    everyone: bool
    # Characters holding a belief that agrees with it.
    holders: list[str] = field(default_factory=list)
    # Characters believing something else about the same thing.
    mistaken: list[str] = field(default_factory=list)


def who_knows(fact: Fact, beliefs: Iterable[Belief]) -> FactKnowledge:
    result = FactKnowledge(fact=fact, everyone=fact.visibility is FactVisibility.COMMON)
    for belief in beliefs:
        if belief.closed_revision is not None or not _covers(belief, fact):
            continue
        target = result.holders if agrees(belief, fact) else result.mistaken
        if belief.holder_entity_id not in target:
            target.append(belief.holder_entity_id)
    return result


# ------------------------------------------------------------------- claims

# Whether the facts bear a claim out: they say the same, they say otherwise, or they
# say nothing about it. A denial is borne out when the facts say otherwise.
Standing = Literal["borne_out", "contradicted", "unconfirmed"]


def claim_standing(claim: EventClaim, facts: Iterable[Fact]) -> Standing:
    covering = [
        fact
        for fact in _current(facts)
        if fact.proposition.subject_id == claim.proposition.subject_id
        and fact.proposition.predicate == claim.proposition.predicate
    ]
    if not covering:
        return "unconfirmed"
    agrees_with_fact = any(fact.proposition.object == claim.proposition.object for fact in covering)
    if claim.modality == "denies":
        return "contradicted" if agrees_with_fact else "borne_out"
    return "borne_out" if agrees_with_fact else "contradicted"


@dataclass(frozen=True)
class ClaimRecord:
    event: Event
    claim: EventClaim
    # Who says it: None when it is the narration itself.
    speaker_id: str | None
    # "speaker" (a character says it) or "narrator" (a character narrates it).
    speaker_role: Literal["speaker", "narrator"] | None
    perceiver_ids: list[str]
    standing: Standing


def claims_of(events: Iterable[Event], facts: Iterable[Fact]) -> list[ClaimRecord]:
    """Every claim still in the story, oldest first, with whether the facts bear it out."""
    fact_list = list(facts)
    found: list[ClaimRecord] = []
    for event in events:
        if event.kind != CLAIM_KIND or event.claim is None or event.retired_revision is not None:
            continue
        speaker = next(
            (p for p in event.participants if p.role in (CLAIM_SPEAKER, CLAIM_NARRATOR)), None
        )
        found.append(
            ClaimRecord(
                event=event,
                claim=event.claim,
                speaker_id=speaker.entity_id if speaker else None,
                speaker_role=(
                    None
                    if speaker is None
                    else "narrator"
                    if speaker.role == CLAIM_NARRATOR
                    else "speaker"
                ),
                perceiver_ids=[
                    p.entity_id for p in event.participants if p.role == CLAIM_PERCEIVER
                ],
                standing=claim_standing(event.claim, fact_list),
            )
        )
    found.sort(key=lambda record: (record.event.created_revision, record.event.id))
    return found


def claim_mentions(record: ClaimRecord, entity_id: str) -> bool:
    """Whether a claim is about an entry, or said by them."""
    proposition = record.claim.proposition
    target = (
        proposition.object.entity_id if isinstance(proposition.object, EntityRefObject) else None
    )
    return entity_id in (proposition.subject_id, target, record.speaker_id)


# ------------------------------------------------------------------ readers


def reading_order(items: Iterable[PlanItem]) -> list[PlanItem]:
    """The plan's scenes in the order a reader meets them: the outline, top to bottom."""
    active = [item for item in items if item.is_active and item.kind is not PlanKind.THREAD]
    children: dict[str | None, list[PlanItem]] = {}
    ids = {item.id for item in active}
    for item in active:
        parent = item.parent_id if item.parent_id in ids else None
        children.setdefault(parent, []).append(item)
    for siblings in children.values():
        siblings.sort(key=lambda item: (item.position, item.title.lower(), item.id))
    scenes: list[PlanItem] = []

    def walk(parent: str | None) -> None:
        for item in children.get(parent, []):
            if item.kind is PlanKind.SCENE:
                scenes.append(item)
            walk(item.id)

    walk(None)
    return scenes


@dataclass
class ReaderStep:
    scene: PlanItem
    number: int
    point_of_view: str | None
    shown: list[Reveal]
    # Facts the reader has been shown plainly by the end of this scene.
    reader_knows: set[str]
    # The reader knows these and the viewpoint character does not: dramatic irony.
    irony: list[str]
    # The viewpoint character knows these secrets and the reader has not been shown them.
    withheld: list[str]
    # Shown plainly here although an earlier scene already did.
    repeated: list[str]
    # Set earlier in story time than the scene before it.
    flashback: bool


def reader_journey(
    scenes: Sequence[PlanItem],
    facts: Iterable[Fact],
    beliefs: Iterable[Belief],
    time_of: dict[str, Interval | None] | None = None,
) -> list[ReaderStep]:
    """Walk the scenes in reading order, keeping track of what the reader has been shown."""
    current = {fact.id: fact for fact in _current(facts)}
    belief_list = list(beliefs)
    shown_plainly: set[str] = set()
    steps: list[ReaderStep] = []
    previous_time: Interval | None = None
    for number, scene in enumerate(scenes, start=1):
        reveals = [reveal for reveal in scene_reveals(scene.details) if reveal.fact_id in current]
        repeated = [
            reveal.fact_id
            for reveal in reveals
            if reveal.how == "shown" and reveal.fact_id in shown_plainly
        ]
        shown_plainly |= {reveal.fact_id for reveal in reveals if reveal.how == "shown"}
        pov = next(iter(scene.refs_as("point_of_view")), None)
        irony: list[str] = []
        withheld: list[str] = []
        if pov is not None:
            knowledge = knowledge_of(pov, current.values(), belief_list)
            known = knowledge.known_ids()
            irony = sorted(fact_id for fact_id in shown_plainly if fact_id not in known)
            withheld = sorted(
                fact.id
                for fact, _how in knowledge.knows
                if fact.visibility is FactVisibility.SECRET and fact.id not in shown_plainly
            )
        moment = (time_of or {}).get(scene.id)
        flashback = bool(
            moment is not None and previous_time is not None and moment.hi < previous_time.lo
        )
        if moment is not None:
            previous_time = moment
        steps.append(
            ReaderStep(
                scene=scene,
                number=number,
                point_of_view=pov,
                shown=reveals,
                reader_knows=set(shown_plainly),
                irony=irony,
                withheld=withheld,
                repeated=repeated,
                flashback=flashback,
            )
        )
    return steps


__all__ = [
    "ClaimRecord",
    "Standing",
    "claim_mentions",
    "claim_standing",
    "claims_of",
    "MAX_REVEALS",
    "REVEALS_KEY",
    "SCENE_TIME_KEY",
    "CharacterKnowledge",
    "FactKnowledge",
    "ReaderStep",
    "Reveal",
    "agrees",
    "knowledge_of",
    "reader_journey",
    "reading_order",
    "scene_reveals",
    "scene_time",
    "who_knows",
    "with_reveals",
    "with_scene_time",
]
