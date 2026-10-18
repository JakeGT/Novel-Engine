"""Knowledge in story order: what a scene's viewpoint character holds by then, and what a
scene uses before its people learn it (KNW-002, CON-006).

Shared by the scene context (which must never give the viewpoint character something
they only learn later), the studio's checks and the plan's checks. Everything is worked
out from beliefs, events and the plan as they are now; nothing here ever blocks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from backend.domain.acquisition import (
    LearnedLater,
    Placement,
    learned_later,
    placement,
    proposition_ends,
    said_before_learned,
)
from backend.domain.canon.assertions import Belief, Fact, FactVisibility
from backend.domain.canon.events import CLAIM_KIND, Event
from backend.domain.canon.plans import PlanItem
from backend.domain.canon.propositions import Proposition
from backend.domain.context import context_settings
from backend.domain.knowledge import reading_order, scene_reveals, scene_time
from backend.domain.plan_checks import PlanFinding
from backend.domain.worldtime import Resolved, Truth, true_at
from backend.services.story_time_service import event_times, fact_span, time_of


@dataclass
class StoryOrder:
    """The plan's scenes and the story's events, placed once for many scenes."""

    items: Sequence[PlanItem]
    events: Sequence[Event]
    where: Placement = field(init=False)
    times: dict[str, Resolved] = field(init=False)
    by_id: dict[str, Event] = field(init=False)

    def __post_init__(self) -> None:
        self.times = event_times(list(self.events))
        times = self.times
        self.where = placement(
            reading_order(self.items),
            self.events,
            scene_when=lambda scene: time_of(scene_time(scene.details), times),
            event_when=lambda event: times[event.id].interval if event.id in times else None,
        )
        self.by_id = {event.id: event for event in self.events}


def story_placement(items: Iterable[PlanItem], events: Sequence[Event]) -> Placement:
    """Each scene's place in reading order and story time, and each event's story time."""
    return StoryOrder(list(items), list(events)).where


def where_learned(item: LearnedLater, where: Placement) -> str:
    """Where it is learned, in plain words: the scene's title, or simply later."""
    title = where.scene_titles.get(item.learned.scene_id or "")
    return f"in “{title}”" if title else "later in the story"


@dataclass(frozen=True)
class SceneSignal:
    """Something a scene uses before the person it is about learns it."""

    who_id: str
    later: LearnedLater
    # The scene is seen through them, so their thoughts and actions are on the page.
    viewpoint: bool
    # The plan shows the reader this plainly in the scene (a reveal).
    planned: bool = False
    # Drafting would give the assistant this as true: a fact about someone or somewhere
    # in the scene, true at its time and not secret, or one the author pinned.
    in_context: bool = False
    # They say it in the scene (a recorded claim, stated or suggested).
    said: bool = False


def scene_signals(
    scene: PlanItem,
    order: StoryOrder,
    facts: Sequence[Fact],
    beliefs: Sequence[Belief],
) -> list[SceneSignal]:
    """What this scene uses before its viewpoint character, or someone who speaks in it,
    learns it."""
    where, by_id, times = order.where, order.by_id, order.times
    claims = [
        event
        for event in order.events
        if event.kind == CLAIM_KIND and event.claim is not None and event.claim.scene_id == scene.id
    ]
    said = said_before_learned(claims, beliefs, facts, by_id, where)
    signals: list[SceneSignal] = []
    pov = next(iter(scene.refs_as("point_of_view")), None)
    if pov is not None:
        cast = {ref.entity_id for ref in scene.refs}
        shown = {reveal.fact_id for reveal in scene_reveals(scene.details) if reveal.how == "shown"}
        pins = set(context_settings(scene.details).pins)
        moment = time_of(scene_time(scene.details), times)
        for item in learned_later(pov, scene.id, beliefs, facts, by_id, where):
            fact = item.fact
            in_context = False
            if fact is not None:
                in_context = f"fact:{fact.id}" in pins
                if (
                    not in_context
                    and fact.visibility is not FactVisibility.SECRET
                    and cast.intersection(proposition_ends(fact.proposition))
                ):
                    in_context = (
                        moment is None or true_at(fact_span(fact, times), moment) is not Truth.FALSE
                    )
            signals.append(
                SceneSignal(
                    who_id=pov,
                    later=item,
                    viewpoint=True,
                    planned=fact is not None and fact.id in shown,
                    in_context=in_context,
                    said=any(
                        early.speaker_id == pov and early.later.belief.id == item.belief.id
                        for early in said
                    ),
                )
            )
    for early in said:
        if early.speaker_id != pov:
            signals.append(
                SceneSignal(who_id=early.speaker_id, later=early.later, viewpoint=False, said=True)
            )
    return signals


def plan_signals(
    items: Sequence[PlanItem],
    facts: Sequence[Fact],
    events: Sequence[Event],
    beliefs: Sequence[Belief],
    names: dict[str, str],
    sentence: Callable[[Proposition], str],
) -> list[PlanFinding]:
    """The plan's own knowledge checks: a reveal shown through a viewpoint character, or a
    claim said by someone, before they learn it. Warnings only."""
    order = StoryOrder(items, events)
    found: list[PlanFinding] = []
    for scene in reading_order(items):
        for signal in scene_signals(scene, order, facts, beliefs):
            who = names.get(signal.who_id, "Someone")
            said_words = sentence(signal.later.belief.proposition)
            when = where_learned(signal.later, order.where)
            if signal.planned:
                found.append(
                    PlanFinding(
                        scene.id,
                        "shown_before_learned",
                        f"This shows the reader “{said_words}” through {who}, who only learns it "
                        f"{when}.",
                    )
                )
            if signal.said:
                found.append(
                    PlanFinding(
                        scene.id,
                        "said_before_learned",
                        f"{who} says “{said_words}” here, but only learns it {when}.",
                    )
                )
    return found


__all__ = [
    "SceneSignal",
    "StoryOrder",
    "plan_signals",
    "scene_signals",
    "story_placement",
    "where_learned",
]
