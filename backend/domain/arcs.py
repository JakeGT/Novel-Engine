"""How a character changes across the story: an arc as a path of stages (STY-006).

An arc in the plan can carry a path: named stages in order, from where the character
starts ("trusts no one") to where they end ("trusts Tobin"). Each stage is reached in
a scene of the plan, or is not placed yet, and may be tied to a story-bible fact that
should hold from that scene on. The first stage is where the character starts, so it
needs no scene.

This follows how writing tools and guides model arcs, kept plain. Plottr draws a
character arc as a lane along the timeline and filters scenes by character; Campfire's
arcs are a character's states linked to timeline events; K. M. Weiland's arcs move a
character from a belief to its opposite through a few turning points, with each act
taking a distinct step. So a stage names a state, not an event, and points at the scene
where it is reached. Arc types (growing, falling, holding firm) are left to the
author's own words in the stage names.

Like a scene's conditions, the path is kept in the arc's details under a reserved key,
so it is part of the plan (intent, never fact), has history and undo, and travels with
what-ifs and archives without a new table.

Checks are derived on read and need no model:

* **Out of order.** A stage reached in a scene that comes before the scene of an
  earlier stage.
* **Not true then.** A stage tied to a fact that is not true at that scene's point in
  story time, or no longer in the story bible.
* **A long stretch.** No scene moves the arc for more than about an act (a quarter of
  the plan's scenes, and at least four): a note, never an error.
* **Not placed.** A stage after the first with no scene yet: a gap to fill.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.domain.canon.plans import MAX_DETAIL_LENGTH, PlanItem, PlanKind
from backend.domain.plan_checks import PlanFinding

PATH_KEY = "_path"
# Guides give an arc a handful of turning points (a start, a few plot points, a
# climax, an end); nine stages covers that and keeps the path inside one detail.
MAX_STAGES = 9
MAX_STAGE_NAME = 60
# A stretch with no stage is a note when it is longer than this share of the plan's
# scenes (about an act) and at least this many scenes.
STRETCH_SHARE = 0.25
MIN_STRETCH = 4

# How a stage's fact stands where the stage is reached.
#   holds: true then.   not_then: not true then.   gone: no longer in the story bible.
#   unclear: cannot tell (the scene has no story time, or the times are loose).
FactStanding = Literal["holds", "not_then", "gone", "unclear"]


class ArcStage(BaseModel):
    """One stage of an arc: a state, where it is reached, and what should hold from then."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_STAGE_NAME)
    # The scene where the character reaches this stage; null while not placed.
    scene_id: str | None = Field(default=None, min_length=1, max_length=200)
    # A story-bible fact that should be true from that scene on.
    fact_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        tidy = " ".join(value.split())
        if not tidy:
            raise ValueError("a stage needs a name")
        return tidy


def arc_path(details: dict[str, str]) -> list[ArcStage]:
    """An arc's stages in order. Anything unreadable is treated as no path."""
    raw = details.get(PATH_KEY)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    found: list[ArcStage] = []
    for item in items if isinstance(items, list) else []:
        try:
            found.append(ArcStage.model_validate(item))
        except ValidationError:
            continue
    return found[:MAX_STAGES]


def path_problem(raw: str) -> str | None:
    """What is wrong with a path as it is kept, in plain words, or None when it reads.

    Paths are written through the plan service, which checks them; this catches one
    written another way (a raw change sent for review) that ``arc_path`` would quietly
    drop or cut short."""
    try:
        items = json.loads(raw)
    except ValueError:
        return "an arc's stages could not be read"
    if not isinstance(items, list):
        return "an arc's stages could not be read"
    if len(items) > MAX_STAGES:
        return f"an arc can have at most {MAX_STAGES} stages"
    names: set[str] = set()
    for item in items:
        try:
            stage = ArcStage.model_validate(item)
        except ValidationError:
            return "an arc's stages could not be read"
        if stage.name.casefold() in names:
            return "each stage needs its own name"
        names.add(stage.name.casefold())
    return None


def path_text(stages: Sequence[ArcStage]) -> str:
    return json.dumps(
        [stage.model_dump(exclude_none=True) for stage in stages],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def path_fits(stages: Sequence[ArcStage]) -> bool:
    """Whether the path fits in one of the plan's details."""
    return len(path_text(stages)) <= MAX_DETAIL_LENGTH


def with_path(details: dict[str, str], stages: Sequence[ArcStage]) -> dict[str, str]:
    kept = {key: value for key, value in details.items() if key != PATH_KEY}
    if stages:
        kept[PATH_KEY] = path_text(stages)
    return kept


def without_scenes(details: dict[str, str], gone: set[str]) -> dict[str, str]:
    """The arc's details with stages placed in removed scenes put back to not placed."""
    stages = arc_path(details)
    if not any(stage.scene_id in gone for stage in stages):
        return details
    return with_path(
        details,
        [
            stage.model_copy(update={"scene_id": None}) if stage.scene_id in gone else stage
            for stage in stages
        ],
    )


@dataclass(frozen=True)
class StageReading:
    """A stage as it stands in the plan: where it is reached, and whether its fact holds."""

    index: int
    name: str
    scene_id: str | None
    # The scene's number in reading order (the first scene is 1); None when not placed
    # or placed in a scene that is no longer in the plan.
    scene_number: int | None
    fact_id: str | None
    # How its fact stands where it is reached; None when it has no fact.
    fact: FactStanding | None


@dataclass(frozen=True)
class ArcReading:
    arc_id: str
    # Whose arc it is: the entries the arc is about.
    about: list[str]
    stages: list[StageReading]


def arcs_with_paths(items: Sequence[PlanItem]) -> list[PlanItem]:
    """Active arcs that have a path, in the order they were made."""
    return [
        item
        for item in items
        if item.is_active and item.kind is PlanKind.ARC and arc_path(item.details)
    ]


def read_arcs(
    items: Sequence[PlanItem],
    scenes: Sequence[PlanItem],
    fact_standing: Callable[[str, PlanItem | None], FactStanding] | None = None,
) -> list[ArcReading]:
    """Every arc's stages with their scene numbers and how their facts stand.

    ``scenes`` are the plan's scenes in reading order (``knowledge.reading_order``).
    ``fact_standing(fact_id, scene)`` says how a fact stands at a scene (None for a
    stage not placed); without it, facts are not judged.
    """
    numbers = {scene.id: number for number, scene in enumerate(scenes, start=1)}
    by_id = {scene.id: scene for scene in scenes}
    readings: list[ArcReading] = []
    for arc in arcs_with_paths(items):
        stages: list[StageReading] = []
        for index, stage in enumerate(arc_path(arc.details)):
            number = numbers.get(stage.scene_id) if stage.scene_id else None
            standing: FactStanding | None = None
            if stage.fact_id is not None:
                standing = (
                    fact_standing(stage.fact_id, by_id.get(stage.scene_id or ""))
                    if fact_standing is not None
                    else "unclear"
                )
            stages.append(
                StageReading(
                    index=index,
                    name=stage.name,
                    scene_id=stage.scene_id,
                    scene_number=number,
                    fact_id=stage.fact_id,
                    fact=standing,
                )
            )
        readings.append(ArcReading(arc_id=arc.id, about=arc.refs_as("about"), stages=stages))
    return readings


def long_stretch(total_scenes: int) -> int:
    """How many scenes between two stages make a long stretch (more than this)."""
    return max(MIN_STRETCH, math.ceil(total_scenes * STRETCH_SHARE))


def arc_findings(
    readings: Sequence[ArcReading],
    scenes: Sequence[PlanItem],
    titles: dict[str, str],
    fact_words: Callable[[str], str] | None = None,
) -> list[PlanFinding]:
    """Stages out of order, facts not true where they are reached, long stretches, gaps.

    Every finding is attached to the arc, so it is listed with it. ``titles`` gives each
    arc's and scene's title; ``fact_words`` says a fact in a short clause.
    """
    by_number = {number: scene for number, scene in enumerate(scenes, start=1)}
    stretch = long_stretch(len(scenes))
    found: list[PlanFinding] = []

    def said(fact_id: str) -> str:
        return fact_words(fact_id) if fact_words is not None else "its fact"

    for arc in readings:
        arc_title = titles.get(arc.arc_id, "This arc")
        latest: StageReading | None = None
        for stage in arc.stages:
            if stage.scene_id is not None and stage.scene_number is None:
                found.append(
                    PlanFinding(
                        arc.arc_id,
                        "arc_stage_scene_gone",
                        f"“{stage.name}” is placed in a scene that is no longer in the plan.",
                    )
                )
            elif stage.scene_number is None and stage.index > 0:
                found.append(
                    PlanFinding(
                        arc.arc_id,
                        "arc_stage_not_placed",
                        f"“{stage.name}” is not placed in a scene yet.",
                        kind="gap",
                    )
                )
            if stage.scene_number is not None:
                if latest is not None and stage.scene_number < (latest.scene_number or 0):
                    found.append(
                        PlanFinding(
                            arc.arc_id,
                            "arc_out_of_order",
                            f"“{stage.name}” is reached in "
                            f"“{titles.get(stage.scene_id or '', 'a scene')}”, which comes "
                            f"before “{titles.get(latest.scene_id or '', 'a scene')}”, where "
                            f"the earlier stage “{latest.name}” is reached.",
                        )
                    )
                else:
                    if latest is not None and latest.scene_number is not None:
                        between = stage.scene_number - latest.scene_number - 1
                        if between > stretch:
                            found.append(
                                PlanFinding(
                                    arc.arc_id,
                                    "arc_long_stretch",
                                    f"Nothing moves “{arc_title}” for {between} scenes, between "
                                    f"“{titles.get(latest.scene_id or '', 'a scene')}” and "
                                    f"“{titles.get(stage.scene_id or '', 'a scene')}”.",
                                    kind="gap",
                                )
                            )
                    latest = stage
            if stage.fact_id is not None and stage.fact == "gone":
                found.append(
                    PlanFinding(
                        arc.arc_id,
                        "arc_fact_gone",
                        f"“{stage.name}” is tied to a fact that is no longer in the story bible.",
                    )
                )
            elif stage.fact_id is not None and stage.fact == "not_then":
                where = (
                    f"when “{by_number[stage.scene_number].title}” happens"
                    if stage.scene_number is not None
                    else "in the story bible now"
                )
                found.append(
                    PlanFinding(
                        arc.arc_id,
                        "arc_fact_not_true",
                        f"“{stage.name}” should hold from here on, but “{said(stage.fact_id)}” "
                        f"is not true {where}.",
                    )
                )
    return found


@dataclass(frozen=True)
class ArcMove:
    """A scene that moves an arc: the stage going in, and the stage reached in it."""

    arc_id: str
    arc_title: str
    about: list[str]
    current: str | None
    reached: str
    fact_id: str | None


def moves_in_scene(items: Sequence[PlanItem], scene_id: str) -> list[ArcMove]:
    """The arcs a scene moves, and from which stage to which.

    The stage going in is the one just before the reached stage in the arc's own order,
    which is the order the author means the character to change in.
    """
    moves: list[ArcMove] = []
    for arc in arcs_with_paths(items):
        stages = arc_path(arc.details)
        for index, stage in enumerate(stages):
            if stage.scene_id == scene_id:
                moves.append(
                    ArcMove(
                        arc_id=arc.id,
                        arc_title=arc.title,
                        about=arc.refs_as("about"),
                        current=stages[index - 1].name if index > 0 else None,
                        reached=stage.name,
                        fact_id=stage.fact_id,
                    )
                )
    return moves


__all__ = [
    "MAX_STAGES",
    "MAX_STAGE_NAME",
    "PATH_KEY",
    "ArcMove",
    "ArcReading",
    "ArcStage",
    "FactStanding",
    "StageReading",
    "arc_findings",
    "arc_path",
    "arcs_with_paths",
    "long_stretch",
    "moves_in_scene",
    "path_fits",
    "path_problem",
    "read_arcs",
    "with_path",
    "without_scenes",
]
