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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from backend.domain.canon.plans import MAX_DETAIL_LENGTH, PlanItem, PlanKind
from backend.domain.plan_checks import PlanFinding

PATH_KEY = "_path"
# Guides give an arc a handful of turning points (a start, a few plot points, a
# climax, an end); nine stages covers that and keeps the path inside one detail.
MAX_STAGES = 9
MAX_STAGE_NAME = 60
# A scale shared by the story's arcs (STY-006): a few of them, each a handful of levels.
MAX_SCALES = 8
MAX_LEVELS = 9
MIN_LEVELS = 2
MAX_SCALE_NAME = 40
MAX_LEVEL_NAME = 40
MAX_SCALE_ABOUT = 200
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
    # Where the character stands on one of the story's scales from here (STY-006): a
    # scale's id and one of its levels, both or neither.
    scale_id: str | None = Field(default=None, min_length=1, max_length=60)
    level: str | None = Field(default=None, min_length=1, max_length=MAX_LEVEL_NAME)

    @model_validator(mode="after")
    def _scale_and_level(self) -> ArcStage:
        if (self.scale_id is None) != (self.level is None):
            raise ValueError("a stage on a scale needs both the scale and its level")
        return self

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
    # Where the character stands on a shared scale from here, if the stage says (STY-006).
    scale_id: str | None = None
    level: str | None = None


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
                    scale_id=stage.scale_id,
                    level=stage.level,
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


# ------------------------------------------------------------------ shared scales
#
# A scale is a state many characters share, such as trust or standing, with named
# levels (STY-006). The design notes call these "project-defined state dimensions": an
# ordered scale (from "none" to "complete") or a set of states with no order ("loyal",
# "wavering", "turned"), and which moves are allowed. Writing tools that track arcs do
# the same with a starting value, a current value and a target; here the levels are
# words, never scores, since a number would claim more than the author said.
# A stage can say where its character stands on a scale from then on; the plan shows,
# for each scale, where each character stands scene by scene, and whether each move is
# towards where their arc ends.


class StateScale(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=MAX_SCALE_NAME)
    # In order from one end to the other when ``ordered``; otherwise just the states.
    levels: list[str] = Field(min_length=MIN_LEVELS, max_length=MAX_LEVELS)
    ordered: bool = True
    # For an ordered scale: a character moves at most one level between stages.
    one_step: bool = False
    # What the scale is about, in the author's words.
    about: str = Field(default="", max_length=MAX_SCALE_ABOUT)

    @field_validator("name", "about")
    @classmethod
    def _tidy_words(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("levels")
    @classmethod
    def _tidy_levels(cls, value: list[str]) -> list[str]:
        tidy = [" ".join(level.split()) for level in value]
        if any(not level or len(level) > MAX_LEVEL_NAME for level in tidy):
            raise ValueError(f"each level needs a name of at most {MAX_LEVEL_NAME} characters")
        if len({level.casefold() for level in tidy}) != len(tidy):
            raise ValueError("give each level its own name")
        return tidy

    @model_validator(mode="after")
    def _steps(self) -> StateScale:
        if self.one_step and not self.ordered:
            raise ValueError("only a scale in order can move one level at a time")
        return self

    def rank(self, level: str | None) -> int | None:
        if level is None:
            return None
        wanted = level.casefold()
        return next((i for i, item in enumerate(self.levels) if item.casefold() == wanted), None)


class ScaleSet(BaseModel):
    """The story's scales, shared by every line of the story like its unit of time."""

    model_config = ConfigDict(extra="forbid")

    scales: list[StateScale] = Field(default_factory=list, max_length=MAX_SCALES)

    @model_validator(mode="after")
    def _unique(self) -> ScaleSet:
        if len({scale.id for scale in self.scales}) != len(self.scales):
            raise ValueError("each scale needs its own id")
        if len({scale.name.casefold() for scale in self.scales}) != len(self.scales):
            raise ValueError("give each scale its own name")
        return self

    def by_id(self) -> dict[str, StateScale]:
        return {scale.id: scale for scale in self.scales}


def read_scales(raw: object) -> ScaleSet:
    """The stored scales; anything unreadable is treated as none."""
    if not raw:
        return ScaleSet()
    try:
        return ScaleSet.model_validate(raw)
    except ValidationError:
        return ScaleSet()


def scale_removal_problem(
    kept: ScaleSet, proposed: ScaleSet, arcs: Iterable[tuple[str, Sequence[ArcStage]]]
) -> str | None:
    """Why ``proposed`` cannot replace ``kept``: it drops a scale, or a level of one,
    that a stage of one of ``arcs`` (each its title and stages, from every line of the
    story) stands on. ``None`` when nothing in use goes."""
    new = proposed.by_id()
    for scale in kept.scales:
        after = new.get(scale.id)
        for title, stages in arcs:
            for stage in stages:
                if stage.scale_id != scale.id:
                    continue
                if after is None:
                    return (
                        f"“{scale.name}” is used by “{title}”. Take it off that arc's stages first."
                    )
                if after.rank(stage.level) is None:
                    return (
                        f"The level “{stage.level}” of “{scale.name}” is used by “{title}”. "
                        "Move that arc's stage to another level first."
                    )
    return None


def scale_findings(
    readings: Sequence[ArcReading], scales: ScaleSet, titles: dict[str, str]
) -> list[PlanFinding]:
    """A stage on a scale that is gone or a level the scale no longer has, and on a
    one-level-at-a-time scale, a move of more than one level between placed stages."""
    known = scales.by_id()
    found: list[PlanFinding] = []
    for arc in readings:
        previous: dict[str, StageReading] = {}
        for stage in sorted(
            (item for item in arc.stages if item.scale_id is not None),
            key=lambda item: (item.scene_number is None, item.scene_number or 0, item.index),
        ):
            scale = known.get(stage.scale_id or "")
            if scale is None:
                found.append(
                    PlanFinding(
                        arc.arc_id,
                        "arc_scale_gone",
                        f"“{stage.name}” is on a scale that is no longer in the story.",
                    )
                )
                continue
            rank = scale.rank(stage.level)
            if rank is None:
                found.append(
                    PlanFinding(
                        arc.arc_id,
                        "arc_level_gone",
                        f"“{stage.name}” is at “{stage.level}” on {scale.name}, which no longer "
                        "has that level.",
                    )
                )
                continue
            before = previous.get(scale.id)
            if (
                before is not None
                and scale.one_step
                and stage.scene_number is not None
                and before.scene_number is not None
            ):
                jump = abs(rank - (scale.rank(before.level) or 0))
                if jump > 1:
                    found.append(
                        PlanFinding(
                            arc.arc_id,
                            "arc_level_jump",
                            f"On {scale.name}, “{stage.name}” moves {jump} levels at once, from "
                            f"“{before.level}” to “{stage.level}”; this scale moves one level "
                            "at a time.",
                        )
                    )
            if stage.scene_number is not None:
                previous[scale.id] = stage
    return found


def lapse_findings(
    readings: Sequence[ArcReading],
    scenes: Sequence[PlanItem],
    fact_standing: Callable[[str, PlanItem | None], FactStanding],
    titles: dict[str, str],
    fact_words: Callable[[str], str] | None = None,
) -> list[PlanFinding]:
    """A stage's fact that stops being true before the next stage is reached (STY-006).

    A stage says its fact should hold from its scene on; this looks at each scene after
    it, up to the scene where the next stage is reached (or to the end, for the last
    one), and says the first where the fact is not true at that scene's story time. A
    scene with no story time cannot say, and is passed over.
    """
    found: list[PlanFinding] = []
    for arc in readings:
        placed = sorted(
            (stage for stage in arc.stages if stage.scene_number is not None),
            key=lambda stage: (stage.scene_number or 0, stage.index),
        )
        for position, stage in enumerate(placed):
            if stage.fact_id is None or stage.fact != "holds" or stage.scene_number is None:
                continue
            reached = stage.scene_number
            following: int = next(
                (
                    later.scene_number
                    for later in placed[position + 1 :]
                    if later.scene_number is not None and later.scene_number > reached
                ),
                len(scenes) + 1,
            )
            for number in range(reached + 1, following):
                scene = scenes[number - 1]
                if fact_standing(stage.fact_id, scene) == "not_then":
                    said = fact_words(stage.fact_id) if fact_words is not None else "its fact"
                    until = (
                        f"before the next stage is reached in “{scenes[following - 1].title}”"
                        if following <= len(scenes)
                        else "and no later stage changes it"
                    )
                    found.append(
                        PlanFinding(
                            arc.arc_id,
                            "arc_fact_lapses",
                            f"“{stage.name}” should hold from “{titles.get(stage.scene_id or '', 'its scene')}” "
                            f"on, but “{said}” is no longer true when “{scene.title}” happens, "
                            f"{until}.",
                        )
                    )
                    break
    return found


@dataclass(frozen=True)
class ScaleStep:
    scene_id: str
    scene_number: int
    level: str
    # For an ordered scale: towards the arc's last level, away from it, or level with it.
    toward: Literal["toward", "away", "same", "reached"] | None = None


@dataclass(frozen=True)
class ScaleRow:
    arc_id: str
    about: list[str]
    # Where the arc starts on the scale (its first stage, if on it), and where it ends.
    start: str | None
    target: str | None
    steps: list[ScaleStep]


@dataclass(frozen=True)
class ScaleProgress:
    scale: StateScale
    rows: list[ScaleRow]


def scale_progress(readings: Sequence[ArcReading], scales: ScaleSet) -> list[ScaleProgress]:
    """For each scale, where each arc's character stands scene by scene (STY-006)."""
    progress: list[ScaleProgress] = []
    for scale in scales.scales:
        rows: list[ScaleRow] = []
        for arc in readings:
            on = [stage for stage in arc.stages if stage.scale_id == scale.id]
            if not on:
                continue
            target = on[-1].level
            target_rank = scale.rank(target)
            start = on[0].level if on[0].index == 0 else None
            steps: list[ScaleStep] = []
            last_rank = scale.rank(start)
            for stage in sorted(
                (item for item in on if item.scene_number is not None and item.scene_id),
                key=lambda item: (item.scene_number or 0, item.index),
            ):
                rank = scale.rank(stage.level)
                toward: Literal["toward", "away", "same", "reached"] | None = None
                if rank is not None and rank == target_rank:
                    toward = "reached"
                elif scale.ordered and rank is not None and target_rank is not None:
                    if last_rank is None:
                        toward = None
                    elif abs(target_rank - rank) < abs(target_rank - last_rank):
                        toward = "toward"
                    elif abs(target_rank - rank) > abs(target_rank - last_rank):
                        toward = "away"
                    else:
                        toward = "same"
                steps.append(
                    ScaleStep(
                        scene_id=stage.scene_id or "",
                        scene_number=stage.scene_number or 0,
                        level=stage.level or "",
                        toward=toward,
                    )
                )
                if rank is not None:
                    last_rank = rank
            rows.append(
                ScaleRow(
                    arc_id=arc.arc_id, about=arc.about, start=start, target=target, steps=steps
                )
            )
        progress.append(ScaleProgress(scale=scale, rows=rows))
    return progress


__all__ = [
    "MAX_SCALES",
    "MAX_STAGES",
    "MAX_STAGE_NAME",
    "PATH_KEY",
    "ArcMove",
    "ArcReading",
    "ArcStage",
    "FactStanding",
    "ScaleProgress",
    "ScaleRow",
    "ScaleSet",
    "ScaleStep",
    "StateScale",
    "StageReading",
    "arc_findings",
    "arc_path",
    "arcs_with_paths",
    "lapse_findings",
    "long_stretch",
    "moves_in_scene",
    "path_fits",
    "path_problem",
    "read_arcs",
    "read_scales",
    "scale_findings",
    "scale_progress",
    "scale_removal_problem",
    "with_path",
    "without_scenes",
]
