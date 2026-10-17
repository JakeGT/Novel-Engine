"""How a thread runs through the plan: where it opens, moves along, and resolves (STY-002).

A thread is opened (``sets_up``) and resolved (``pays_off``) by plan items. Between
the two, a beat or scene may say it *moves the thread along*: a clue found, a promise
renewed, a question sharpened. Those steps are kept in the item's details under a
reserved key, like a scene's conditions, so no new column or operation is needed and
every step has history and undo like the rest of the plan.

The sequence of a thread's steps is derived on read in outline order, never stored.
Writing guides commonly give a subplot at least three points (one to establish it,
one to develop it, one to resolve it), so a thread that opens and resolves with
scenes between and nothing moving it along is listed as a gap: a thing to look at,
never an error.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from backend.domain.canon.plans import MAX_THREAD_LINKS, PlanItem, PlanKind

ADVANCES_KEY = "_advances"

Step = Literal["opens", "advances", "resolves"]


@dataclass(frozen=True)
class ThreadStep:
    plan_id: str
    step: Step


def thread_advances(details: dict[str, str]) -> list[str]:
    """The threads an item moves along. Anything unreadable is treated as none."""
    raw = details.get(ADVANCES_KEY)
    if not raw:
        return []
    try:
        found = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(found, list):
        return []
    seen: list[str] = []
    for item in found:
        if isinstance(item, str) and item and item not in seen:
            seen.append(item)
    return seen[:MAX_THREAD_LINKS]


def with_advances(details: dict[str, str], thread_ids: Iterable[str]) -> dict[str, str]:
    kept = {key: value for key, value in details.items() if key != ADVANCES_KEY}
    ids = list(dict.fromkeys(thread_ids))
    if ids:
        kept[ADVANCES_KEY] = json.dumps(ids, separators=(",", ":"))
    return kept


def outline_order(items: Iterable[PlanItem]) -> list[PlanItem]:
    """Every active item except threads, top to bottom as the outline reads.

    The same walk as the reader's order of scenes (``knowledge.reading_order``), with
    stories, arcs and beats kept in place, since any of them can carry a thread.
    """
    active = [item for item in items if item.is_active and item.kind is not PlanKind.THREAD]
    ids = {item.id for item in active}
    children: dict[str | None, list[PlanItem]] = {}
    for item in active:
        parent = item.parent_id if item.parent_id in ids else None
        children.setdefault(parent, []).append(item)
    for siblings in children.values():
        siblings.sort(key=lambda item: (item.position, item.title.lower(), item.id))
    ordered: list[PlanItem] = []

    def walk(parent: str | None) -> None:
        for item in children.get(parent, []):
            ordered.append(item)
            walk(item.id)

    walk(None)
    return ordered


def thread_progress(items: Iterable[PlanItem]) -> dict[str, list[ThreadStep]]:
    """Each thread's steps in outline order. An item that opens and resolves one has both."""
    listed = list(items)
    threads = {item.id for item in listed if item.is_active and item.kind is PlanKind.THREAD}
    progress: dict[str, list[ThreadStep]] = {thread_id: [] for thread_id in threads}
    for item in outline_order(listed):
        advancing = thread_advances(item.details)
        for thread_id in threads:
            if thread_id in item.sets_up:
                progress[thread_id].append(ThreadStep(item.id, "opens"))
            if thread_id in advancing:
                progress[thread_id].append(ThreadStep(item.id, "advances"))
            if thread_id in item.pays_off:
                progress[thread_id].append(ThreadStep(item.id, "resolves"))
    return progress


def never_moved_along(steps: list[ThreadStep], order: list[PlanItem]) -> bool:
    """Whether a thread is opened and resolved, with scenes between, and nothing moving it.

    ``order`` is the outline order (``outline_order``). A thread opened and resolved in
    the same scene, or in scenes next to each other, has no room to move and is fine.
    """
    if any(s.step == "advances" for s in steps):
        return False
    where = {item.id: index for index, item in enumerate(order)}
    starts = [where[s.plan_id] for s in steps if s.step == "opens" and s.plan_id in where]
    ends = [where[s.plan_id] for s in steps if s.step == "resolves" and s.plan_id in where]
    if not starts or not ends:
        return False
    first, last = min(starts), max(ends)
    between = [item for item in order[first + 1 : last] if item.kind is PlanKind.SCENE]
    return len(between) > 0


__all__ = [
    "ADVANCES_KEY",
    "ThreadStep",
    "never_moved_along",
    "outline_order",
    "thread_advances",
    "thread_progress",
    "with_advances",
]
