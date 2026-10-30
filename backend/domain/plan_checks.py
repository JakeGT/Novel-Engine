"""Checks on the order of a plan, and the promises it makes the reader (STY-007).

Everything here is derived on read from the plan, the reader ledger (each scene's
reveals) and the current facts, so it needs no model and cannot drift.

* **Too early.** A scene can be marked as where a fact comes out. Showing that fact
  plainly in an earlier scene (reading order) gives it away early. Hints before it
  are clues, and a reveal with no clue before it is noted, since guides on planting
  clues ask that the reveal scene not be the first place a clue appears.
* **Out of order.** A thread resolved, or moved along, before anything opens it.
* **Promises.** Each opened thread and each hinted fact is a promise to the reader.
  The list says where each was made, where it is kept, and how many scenes the
  reader waits between the two (the reading distance), or how long it has been
  open, so the author can see what is still owed (Chekhov's gun).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from backend.domain.canon.assertions import Fact
from backend.domain.canon.phrasing import phrase
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.knowledge import scene_reveals
from backend.domain.threads import outline_order, thread_advances


@dataclass(frozen=True)
class PlanFinding:
    """Something to look at in the plan, said in an author's words."""

    plan_id: str
    code: str
    message: str
    # gap: something not filled in yet. problem: something out of order or not ready.
    kind: Literal["gap", "problem"] = "problem"


@dataclass(frozen=True)
class Promise:
    """A thread opened or a fact hinted: something the reader is owed."""

    kind: Literal["thread", "clue"]
    # The thread's plan id, or the fact's id.
    subject_id: str
    # Where it is first made, and its place in reading order (scene 1 is the first).
    made_in: str
    made_at: int
    # Where it is moved along (or hinted again) before it is kept.
    moved_along: list[str] = field(default_factory=list)
    kept_in: str | None = None
    kept_at: int | None = None
    # Scenes the reader waits from the promise to its keeping; when it is not kept,
    # scenes since it was made, to the end of the plan.
    distance: int = 0
    # Kept before it is made: a thread resolved before it is opened.
    out_of_order: bool = False

    @property
    def kept(self) -> bool:
        return self.kept_in is not None and not self.out_of_order


def fact_sentence(fact: Fact, names: dict[str, str]) -> str:
    """A fact as a short clause: "Ines Varga is located at Kestrel Point"."""
    proposition = fact.proposition
    subject = names.get(proposition.subject_id, "Someone")
    obj = proposition.object
    target = names.get(getattr(obj, "entity_id", "") or "")
    if target is None:
        value = getattr(obj, "value", None)
        target = str(value) if value is not None else ""
    return f"{subject} {phrase(proposition.predicate)} {target}".strip()


def _scene_places(order: Sequence[PlanItem]) -> dict[str, int]:
    """Each outline item's place in reading order, counted in scenes.

    A scene is its own number. A story, arc or beat takes the number of the first
    scene that comes after it, so a thread a beat opens is opened where its scenes
    begin; one with no scene after it takes the last number.
    """
    total = sum(1 for item in order if item.kind is PlanKind.SCENE)
    places: dict[str, int] = {}
    seen = 0
    for item in order:
        if item.kind is PlanKind.SCENE:
            seen += 1
            places[item.id] = seen
        else:
            places[item.id] = min(seen + 1, max(total, 1))
    return places


def reveal_findings(
    scenes: Sequence[PlanItem], facts: Iterable[Fact], names: dict[str, str]
) -> list[PlanFinding]:
    """Reveals given away early, planned twice, or with no clue before them.

    ``scenes`` are in reading order (``knowledge.reading_order``). Only facts that are
    still in the story count.
    """
    current = {fact.id: fact for fact in facts if fact.closed_revision is None}
    by_fact: dict[str, list[tuple[int, PlanItem, str, bool]]] = {}
    for number, scene in enumerate(scenes):
        for reveal in scene_reveals(scene.details):
            if reveal.fact_id in current:
                by_fact.setdefault(reveal.fact_id, []).append(
                    (number, scene, reveal.how, reveal.main_reveal)
                )
    found: list[PlanFinding] = []
    for fact_id, uses in by_fact.items():
        mains = [use for use in uses if use[3]]
        if not mains:
            continue
        said = fact_sentence(current[fact_id], names)
        first_number, first_main, _how, _main = mains[0]
        for _number, scene, _how2, _main2 in mains[1:]:
            found.append(
                PlanFinding(
                    scene.id,
                    "planned_reveal_twice",
                    f"“{said}” is marked to come out here and also in “{first_main.title}”, "
                    "earlier. Choose one.",
                )
            )
        for number, scene, how, main in uses:
            if number < first_number and how == "shown" and not main:
                found.append(
                    PlanFinding(
                        scene.id,
                        "reveals_too_early",
                        f"This shows the reader “{said}” plainly, before “{first_main.title}”, "
                        "where it is meant to come out.",
                    )
                )
        if not any(number < first_number and how == "hinted" for number, _s, how, _m in uses):
            found.append(
                PlanFinding(
                    first_main.id,
                    "reveal_without_clue",
                    f"“{said}” comes out here with no hint in an earlier scene. A clue before "
                    "it lets the reader see it coming.",
                    kind="gap",
                )
            )
    return found


def order_findings(items: Sequence[PlanItem]) -> list[PlanFinding]:
    """Threads resolved or moved along before anything opens them, in outline order."""
    active = [item for item in items if item.is_active]
    titles = {item.id: item.title for item in active if item.kind is PlanKind.THREAD}
    order = outline_order(active)
    where = {item.id: index for index, item in enumerate(order)}
    found: list[PlanFinding] = []
    for thread_id, title in titles.items():
        openers = [item for item in order if thread_id in item.sets_up]
        if not openers:
            continue
        first_open = where[openers[0].id]
        for item in order:
            if where[item.id] >= first_open:
                break
            if thread_id in item.pays_off:
                found.append(
                    PlanFinding(
                        item.id,
                        "resolves_before_opening",
                        f"This resolves “{title}” before “{openers[0].title}” opens it.",
                    )
                )
            elif thread_id in thread_advances(item.details):
                found.append(
                    PlanFinding(
                        item.id,
                        "moves_along_before_opening",
                        f"This moves “{title}” along before “{openers[0].title}” opens it.",
                    )
                )
    return found


def promises(
    items: Sequence[PlanItem],
    scenes: Sequence[PlanItem],
    facts: Iterable[Fact],
) -> list[Promise]:
    """Every promise the plan makes the reader, in the order it is made.

    ``scenes`` are the plan's scenes in reading order. A thread is promised where it
    is first opened and kept where it is first resolved after that. A fact is promised
    where it is first hinted, if the reader has not already been shown it, and kept
    where a later scene shows it plainly.
    """
    active = [item for item in items if item.is_active]
    order = outline_order(active)
    places = _scene_places(order)
    total = len(scenes)
    found: list[Promise] = []

    for thread in sorted(
        (item for item in active if item.kind is PlanKind.THREAD),
        key=lambda item: (item.position, item.created_revision, item.id),
    ):
        openers = [item for item in order if thread.id in item.sets_up]
        if not openers:
            continue
        made = openers[0]
        made_at = places[made.id]
        after = order[order.index(made) :]
        keeper = next((item for item in after if thread.id in item.pays_off), None)
        early = next((item for item in order if thread.id in item.pays_off), None)
        out_of_order = keeper is None and early is not None
        kept = keeper if keeper is not None else early
        moved = [
            item.id
            for item in after
            if thread.id in thread_advances(item.details)
            and (keeper is None or order.index(item) < order.index(keeper))
        ]
        kept_at = places[kept.id] if kept is not None else None
        found.append(
            Promise(
                kind="thread",
                subject_id=thread.id,
                made_in=made.id,
                made_at=made_at,
                moved_along=moved,
                kept_in=kept.id if kept is not None else None,
                kept_at=kept_at,
                distance=abs(kept_at - made_at) if kept_at is not None else max(total - made_at, 0),
                out_of_order=out_of_order,
            )
        )

    current = {fact.id for fact in facts if fact.closed_revision is None}
    first_hint: dict[str, int] = {}
    hinted_again: dict[str, list[int]] = {}
    shown_at: dict[str, list[int]] = {}
    for number, scene in enumerate(scenes, start=1):
        for reveal in scene_reveals(scene.details):
            if reveal.fact_id not in current:
                continue
            if reveal.how == "hinted":
                if reveal.fact_id in first_hint:
                    hinted_again.setdefault(reveal.fact_id, []).append(number)
                else:
                    first_hint[reveal.fact_id] = number
            else:
                shown_at.setdefault(reveal.fact_id, []).append(number)
    for fact_id, hint_at in first_hint.items():
        shown = shown_at.get(fact_id, [])
        if any(at < hint_at for at in shown):
            # The reader was already told; a later hint promises nothing new.
            continue
        kept_at = next((at for at in shown if at > hint_at), None)
        found.append(
            Promise(
                kind="clue",
                subject_id=fact_id,
                made_in=scenes[hint_at - 1].id,
                made_at=hint_at,
                moved_along=[
                    scenes[at - 1].id
                    for at in hinted_again.get(fact_id, [])
                    if kept_at is None or at < kept_at
                ],
                kept_in=scenes[kept_at - 1].id if kept_at is not None else None,
                kept_at=kept_at,
                distance=(kept_at - hint_at) if kept_at is not None else max(total - hint_at, 0),
            )
        )
    found.sort(key=lambda promise: (promise.made_at, promise.kind != "thread"))
    return found


__all__ = [
    "PlanFinding",
    "Promise",
    "fact_sentence",
    "order_findings",
    "promises",
    "reveal_findings",
]
