"""How a character came to know something, and when (KNW-002); knowledge used before
it was learned (CON-006).

A belief points at how it was acquired through ``source_ref``: an event. The event's
kind and the holder's part in it say how: told, overheard, seen, worked out from other
things, or heard said in a claim. Any other event is "from" that event; a belief that
points at no event was simply recorded by the author.

When it was learned comes from the event: its story time when it has one, else the
time of the scene it happens in, else only that scene's place in the reading order. A
character is taken to learn something *after* a scene only when that is certain: by
story time when both have one and the times do not overlap, otherwise by reading order
when both sit in the plan. Anything that cannot be placed is treated as known all along,
so nothing here ever warns on a guess.

Everything is derived on read from beliefs, events and the plan, so it cannot drift,
and nothing here ever blocks a change (plan/13: only complete, current evaluation of a
rule the author made blocking may block).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from backend.domain.canon.assertions import Belief, Fact, FactStatus, FactVisibility
from backend.domain.canon.events import (
    CLAIM_KIND,
    CLAIM_NARRATOR,
    CLAIM_SPEAKER,
    INFERRED_KIND,
    Event,
)
from backend.domain.canon.plans import PlanItem
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.worldtime import Interval, Order, compare

# The event kinds the knowledge workflows write (``knowledge_service``).
TELLING_KIND = "telling"
WITNESSED_KIND = "witnessed"

How = Literal["told", "overheard", "saw", "worked_out", "heard_said", "event", "recorded"]

# What a claim says that its speaker can only say knowingly: a question or a denial is
# not acting on the thing.
_KNOWING_MODALITIES = ("states", "suggests")


@dataclass(frozen=True)
class Learned:
    """How and when one belief was acquired."""

    belief_id: str
    holder_id: str
    how: How
    event_id: str | None = None
    event_name: str | None = None
    # The scene the event happens in (a claim's own scene for a claim).
    scene_id: str | None = None
    # Who told them, or who said it, when someone did.
    source_id: str | None = None
    # What it was worked out from, for an inference: fact and belief ids.
    premises: tuple[str, ...] = ()


def how_learned(belief: Belief, events: dict[str, Event]) -> Learned:
    """How a belief was acquired, from the event its ``source_ref`` names."""
    event = events.get(belief.source_ref or "")
    if event is None or event.retired_revision is not None:
        return Learned(belief.id, belief.holder_entity_id, "recorded")
    roles = {p.role for p in event.participants if p.entity_id == belief.holder_entity_id}
    source = next(
        (
            p.entity_id
            for p in event.participants
            if p.role in (CLAIM_SPEAKER, CLAIM_NARRATOR) and p.entity_id != belief.holder_entity_id
        ),
        None,
    )
    how: How
    if event.kind == TELLING_KIND:
        how = "overheard" if "overheard" in roles else "told"
    elif event.kind == WITNESSED_KIND:
        how = "saw"
    elif event.kind == INFERRED_KIND:
        how = "worked_out"
    elif event.kind == CLAIM_KIND:
        how = "heard_said"
    else:
        how = "event"
    scene_id = event.scene_id or (event.claim.scene_id if event.claim is not None else None)
    return Learned(
        belief_id=belief.id,
        holder_id=belief.holder_entity_id,
        how=how,
        event_id=event.id,
        event_name=event.name,
        scene_id=scene_id,
        source_id=source,
        premises=tuple(event.premises),
    )


@dataclass(frozen=True)
class Placement:
    """Where things sit: each scene's place in reading order and story time, and each
    event's story time."""

    scene_index: dict[str, int] = field(default_factory=dict)
    scene_moment: dict[str, Interval | None] = field(default_factory=dict)
    event_moment: dict[str, Interval | None] = field(default_factory=dict)
    scene_titles: dict[str, str] = field(default_factory=dict)


def placement(
    scenes: Sequence[PlanItem],
    events: Iterable[Event],
    *,
    scene_when: Callable[[PlanItem], Interval | None],
    event_when: Callable[[Event], Interval | None],
) -> Placement:
    """``scenes`` in reading order (``knowledge.reading_order``)."""
    return Placement(
        scene_index={scene.id: number for number, scene in enumerate(scenes)},
        scene_moment={scene.id: scene_when(scene) for scene in scenes},
        event_moment={event.id: event_when(event) for event in events},
        scene_titles={scene.id: scene.title for scene in scenes},
    )


def learned_moment(learned: Learned, where: Placement) -> Interval | None:
    """The story time it was learned: the event's own, else its scene's."""
    if learned.event_id is not None:
        moment = where.event_moment.get(learned.event_id)
        if moment is not None:
            return moment
    if learned.scene_id is not None:
        return where.scene_moment.get(learned.scene_id)
    return None


def learned_after(learned: Learned, scene_id: str, where: Placement) -> bool:
    """Whether it is certainly learned after the scene: by story time when both have one
    and they do not overlap, else by reading order when both are in the plan."""
    if learned.event_id is None:
        return False
    moment = learned_moment(learned, where)
    order = compare(moment, where.scene_moment.get(scene_id))
    if order is Order.AFTER:
        return True
    if order is Order.BEFORE:
        return False
    if learned.scene_id is None:
        return False
    at = where.scene_index.get(learned.scene_id)
    here = where.scene_index.get(scene_id)
    return at is not None and here is not None and at > here


def _key(proposition: Proposition) -> tuple[str, str, str]:
    return (
        proposition.subject_id,
        proposition.predicate,
        proposition.object.model_dump_json(),
    )


@dataclass(frozen=True)
class LearnedLater:
    """Something a character holds, but only comes to hold after a scene."""

    belief: Belief
    learned: Learned
    # The current fact it agrees with, when the story says it is so.
    fact: Fact | None = None


def learned_later(
    holder_id: str,
    scene_id: str,
    beliefs: Iterable[Belief],
    facts: Iterable[Fact],
    events: dict[str, Event],
    where: Placement,
) -> list[LearnedLater]:
    """What ``holder_id`` believes but only learns after ``scene_id``.

    A thing learned twice counts from the first time; something everyone knows is known
    all along; a closed belief is not held.
    """
    current = [
        fact
        for fact in facts
        if fact.status is FactStatus.ASSERTED and fact.closed_revision is None
    ]
    common = {
        _key(fact.proposition) for fact in current if fact.visibility is FactVisibility.COMMON
    }
    agreeing = {_key(fact.proposition): fact for fact in current}
    held: dict[tuple[str, str, str], list[Belief]] = {}
    for belief in beliefs:
        if belief.holder_entity_id == holder_id and belief.closed_revision is None:
            held.setdefault(_key(belief.proposition), []).append(belief)
    found: list[LearnedLater] = []
    for key, same in held.items():
        if key in common:
            continue
        learned = [how_learned(belief, events) for belief in same]
        if not all(learned_after(item, scene_id, where) for item in learned):
            continue
        first = min(
            zip(same, learned, strict=True),
            key=lambda pair: (
                where.scene_index.get(pair[1].scene_id or "", 10**9),
                pair[0].created_revision,
            ),
        )
        found.append(LearnedLater(belief=first[0], learned=first[1], fact=agreeing.get(key)))
    found.sort(key=lambda item: (item.belief.created_revision, item.belief.id))
    return found


def held_at(
    holder_id: str,
    scene_id: str,
    beliefs: Iterable[Belief],
    events: dict[str, Event],
    where: Placement,
) -> list[Belief]:
    """Every belief, less the holder's beliefs they only come to hold after the scene, so
    knowledge read for that scene never includes what is learned later."""
    items = list(beliefs)
    later = {
        item.belief.id for item in learned_later(holder_id, scene_id, items, [], events, where)
    }
    if not later:
        return items
    # A belief learned later stands for every belief with the same proposition.
    keys = {_key(belief.proposition) for belief in items if belief.id in later}
    return [
        belief
        for belief in items
        if not (belief.holder_entity_id == holder_id and _key(belief.proposition) in keys)
    ]


def proposition_ends(proposition: Proposition) -> list[str]:
    """The entries a proposition names: its subject and, when it is one, its object."""
    ends = [proposition.subject_id]
    if isinstance(proposition.object, EntityRefObject):
        ends.append(proposition.object.entity_id)
    return ends


@dataclass(frozen=True)
class SaidTooEarly:
    """A claim in a scene that says something its speaker only learns later."""

    scene_id: str
    event: Event
    speaker_id: str
    later: LearnedLater


def said_before_learned(
    claims: Iterable[Event],
    beliefs: Sequence[Belief],
    facts: Sequence[Fact],
    events: dict[str, Event],
    where: Placement,
) -> list[SaidTooEarly]:
    """Claims made in a scene by a character (speaking or narrating) who only comes to
    believe what they say after that scene. Questions and denials are left alone."""
    found: list[SaidTooEarly] = []
    for event in claims:
        claim = event.claim
        if (
            event.kind != CLAIM_KIND
            or claim is None
            or claim.scene_id is None
            or event.retired_revision is not None
            or claim.modality not in _KNOWING_MODALITIES
        ):
            continue
        speaker = next(
            (p.entity_id for p in event.participants if p.role in (CLAIM_SPEAKER, CLAIM_NARRATOR)),
            None,
        )
        if speaker is None:
            continue
        key = _key(claim.proposition)
        for item in learned_later(speaker, claim.scene_id, beliefs, facts, events, where):
            if _key(item.belief.proposition) == key:
                found.append(SaidTooEarly(claim.scene_id, event, speaker, item))
    return found


__all__ = [
    "How",
    "Learned",
    "LearnedLater",
    "Placement",
    "SaidTooEarly",
    "held_at",
    "how_learned",
    "learned_after",
    "learned_later",
    "learned_moment",
    "placement",
    "proposition_ends",
    "said_before_learned",
]
