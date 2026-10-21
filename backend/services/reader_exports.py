"""Separate parts of the reader's guide: characters, places, a timeline, a recap (QRY-002).

Each part follows the guide's own rules (``KnowledgeService.guide``): nothing the reader
has not been shown by the chosen scene, nothing dated after it, nobody who has not yet
appeared, and with a viewpoint only what that character knows. The characters and
places parts are the guide's sections for those kinds of entry. The timeline and the
recap need rules of their own, kept deliberately narrow:

* **Timeline**: the scenes read so far that are placed in story time, and the events
  that happened in them (an event read from a scene, or set in it), in story-time order.
  An event not tied to a scene read so far is left out even when it is dated earlier:
  the story may not have told the reader of it yet. With a viewpoint, only events that
  character took part in. Claims and conclusions are not events in the world and are
  left out.
* **Recap**: one entry per scene read so far, in reading order: whose eyes, where, who
  is there (those who have appeared), and what it shows the reader plainly. Hints are
  left out (a hint is not something the reader knows), and so is the plan's summary of
  the scene: it is the author's note and may name what the reader has not been shown.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from backend.domain.canon.assertions import Fact
from backend.domain.canon.events import CLAIM_KIND, INFERRED_KIND, Event
from backend.domain.knowledge import ReaderStep, scene_time
from backend.domain.worldtime import Interval, Resolved, describe

Part = Literal["everything", "characters", "places", "timeline", "recap"]
PARTS: tuple[str, ...] = ("everything", "characters", "places", "timeline", "recap")

# The part's name in a title and in the file name; how it is said in a sentence.
PART_TITLES: dict[str, str] = {
    "everything": "The story so far",
    "characters": "Characters so far",
    "places": "Places so far",
    "timeline": "Timeline so far",
    "recap": "Recap so far",
}
PART_TYPES: dict[str, str] = {"characters": "Character", "places": "Location"}


@dataclass(frozen=True)
class Entry:
    name: str
    lines: list[str]


@dataclass(frozen=True)
class Section:
    entity_type: str
    heading: str
    entries: list[Entry]


def leaves_out(part: str, who: str | None) -> str:
    """What a part leaves out, in one plain sentence (or two)."""
    if who:
        base = f"Only what {who} knows."
    else:
        base = (
            "Leaves out secrets not yet shown to the reader, anything dated after this "
            "scene, and anyone who has not yet appeared."
        )
    if part == "timeline":
        return base + (
            " Only scenes read so far and what happened in them; events the story has not "
            "yet told are left out."
        )
    if part == "recap":
        return base + " Hints, and the plan's own notes on each scene, are left out."
    return base


def timeline_sections(
    steps: Sequence[ReaderStep],
    events: Sequence[Event],
    times: Mapping[str, Resolved],
    time_of: Callable[[Any], Interval | None],
    unit: str,
    names: Mapping[str, str],
    viewpoint: str | None,
) -> list[Section]:
    """Scenes read so far and the events in them, in story-time order."""
    read = {step.scene.id: step for step in steps}
    event_names = {event.id: event.name for event in events}
    placed: list[tuple[float, float, int, Entry]] = []
    unplaced: list[Entry] = []
    for step in steps:
        when = scene_time(step.scene.details)
        moment = time_of(when) if when is not None else None
        entry = Entry(
            f"Scene {step.number}: {step.scene.title}",
            [f"When: {describe(when, unit=unit, event_name=event_names.get)}"]
            if when is not None and moment is not None
            else ["No time is given for this scene in the plan."],
        )
        if (
            viewpoint is not None
            and viewpoint not in step.scene.refs_as("point_of_view")
            and (viewpoint not in step.scene.refs_as("participant"))
        ):
            continue
        if moment is None:
            unplaced.append(entry)
        else:
            placed.append((moment.lo, moment.hi, step.number, entry))
    for event in events:
        if event.retired_revision is not None or event.kind in (CLAIM_KIND, INFERRED_KIND):
            continue
        found = read.get(event.scene_id or "")
        if found is None:
            continue
        people = [item.entity_id for item in event.participants]
        if viewpoint is not None and viewpoint not in people:
            continue
        lines = [f"In scene {found.number}, “{found.scene.title}”."]
        named = [names[person] for person in dict.fromkeys(people) if person in names]
        if named:
            lines.append("Who: " + ", ".join(named) + ".")
        resolved = times.get(event.id)
        interval = resolved.interval if resolved is not None else None
        if event.when is not None and interval is not None:
            lines.insert(0, f"When: {describe(event.when, unit=unit, event_name=event_names.get)}")
            placed.append((interval.lo, interval.hi, found.number, Entry(event.name, lines)))
        else:
            unplaced.append(Entry(event.name, lines))
    placed.sort(key=lambda item: (item[0], item[1], item[2], item[3].name.lower()))
    sections: list[Section] = []
    if placed:
        sections.append(Section("timeline", "In story time", [item[3] for item in placed]))
    if unplaced:
        sections.append(Section("timeline", "Not placed in story time", unplaced))
    return sections


def recap_sections(
    steps: Sequence[ReaderStep],
    facts: Mapping[str, Fact],
    appeared: set[str],
    names: Mapping[str, str],
    known: set[str] | None,
    sentence: Callable[[Any, dict[str, str]], str],
) -> list[Section]:
    """One entry per scene read so far: whose eyes, where, who is there, what it shows."""
    entries: list[Entry] = []
    for step in steps:
        lines: list[str] = []
        pov = step.point_of_view
        if pov and pov in appeared and pov in names:
            lines.append(f"Seen through {names[pov]}’s eyes.")
        place = next(iter(step.scene.refs_as("place")), None)
        if place and place in names:
            lines.append(f"Where: {names[place]}.")
        present = [
            names[person]
            for person in dict.fromkeys(step.scene.refs_as("participant"))
            if person in names and person != pov
        ]
        if present:
            lines.append("Also there: " + ", ".join(present) + ".")
        if step.flashback:
            lines.append("Set earlier in story time than the scene before it.")
        for reveal in step.shown:
            if reveal.how != "shown" or reveal.fact_id not in facts:
                continue
            if known is not None and reveal.fact_id not in known:
                continue
            lines.append("Shows: " + sentence(facts[reveal.fact_id].proposition, dict(names)))
        if not any(line.startswith("Shows: ") for line in lines):
            lines.append("Shows the reader nothing plainly yet.")
        entries.append(Entry(f"Scene {step.number}: {step.scene.title}", lines))
    return [Section("recap", "Scene by scene", entries)] if entries else []


__all__ = [
    "PARTS",
    "PART_TITLES",
    "PART_TYPES",
    "Part",
    "leaves_out",
    "recap_sections",
    "timeline_sections",
]
