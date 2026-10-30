"""What the checks against a scene's prose need to know, read from the story (STY-005,
SCN-007, CON-004, CON-014).

The studio's brief says who is in the scene, what it should leave true, the scene
before it in reading order and how far before it is in story time, who has what at
the scene's time, and how its people look in their accepted details and in the
accepted descriptions of their pictures. The checks themselves are in
``backend.domain.drafting`` and need no model; this only gathers, and never reads
the prose.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from backend.domain.appearance import AgeKnown, AppearanceDetail, detail_words
from backend.domain.canon.plans import PlanItem
from backend.domain.continuity import (
    AGE_KINDS,
    BIRTH_KINDS,
    BIRTH_ROLES,
    PLAIN_LIFE_KINDS,
    POSSESSION_KINDS,
    StoryParts,
    counts_whole_units,
    movement_context,
)
from backend.domain.drafting import (
    AfterShown,
    DraftBrief,
    Holding,
    Supply,
    TimeNeighbour,
    days_between,
)
from backend.domain.knowledge import reading_order, scene_time
from backend.domain.media import link_applies_at
from backend.domain.media_observations import ObservationKind
from backend.domain.movement import Elsewhere, Kept, MovementBrief, PlaceKnown
from backend.domain.scene_conditions import condition_reading, scene_conditions
from backend.domain.worldtime import (
    At,
    Interval,
    Truth,
    describe,
    resolve,
    true_at,
)
from backend.services.story_time_service import Story, anchor_lookup, event_times, fact_span

APPEARANCE_PREDICATE = "HAS_DETAIL"
STATUS_PREDICATE = "HAS_STATUS"
# Owning is not holding: someone may carry a thing another owns. Only these say it is
# in someone's hands.
IN_HAND = POSSESSION_KINDS - {"OWNS"}
# Statuses that say a thing cannot be used any more.
GONE_WORDS = frozenset(
    {
        "used up",
        "used",
        "spent",
        "consumed",
        "destroyed",
        "broken",
        "lost",
        "gone",
        "empty",
        "burned",
        "burnt",
        "sunk",
        "shattered",
        "eaten",
        "drunk",
    }
)
MAX_HOLDINGS = 200
MAX_DETAILS_PER_PERSON = 6
MAX_LINKS = 500


def _true_then(span: Any, moment: Interval | None) -> bool:
    """Certainly true at the scene's time; with no time, only what is true at every time."""
    if moment is None:
        return not span.stated
    return true_at(span, moment) is Truth.TRUE


def _possible_then(span: Any, moment: Interval | None) -> bool:
    if moment is None:
        return True
    return true_at(span, moment) is not Truth.FALSE


async def add_prose_signals(
    brief: DraftBrief,
    *,
    reader: Any,
    workspace_id: str,
    project_id: str,
    scene: PlanItem,
    story: Story,
    first: dict[str, str],
    names_of: dict[str, tuple[str, ...]],
    items: Sequence[PlanItem],
    unit: str,
    pictures: Any = None,
    clocks: Any = None,
) -> None:
    """Fill the brief's fields for the checks against the prose, in place. ``clocks`` (the
    story's clock and travel table) lets travel read from the words be checked (SCN-007)."""
    times = event_times(story.events)
    when = scene_time(scene.details)
    moment = resolve(when, anchor_lookup(times)).interval if when is not None else None
    people = [
        entity_id
        for entity_id in dict.fromkeys(
            [*scene.refs_as("point_of_view"), *scene.refs_as("participant")]
        )
        if entity_id in first
    ]
    brief.people = tuple(
        first[entity_id]
        for entity_id in people
        if story.entities.get(entity_id) is not None
        and story.entities[entity_id].type == "Character"
    )
    for entity_id in people:
        brief.ids.setdefault(first[entity_id], entity_id)
    brief.after = _after(scene, story, first)
    brief.previous = _previous(scene, items, times, unit, story)
    brief.holdings = _holdings(story, moment, set(people), first, names_of, brief)
    brief.supplies = _supplies(story, moment, first, names_of, unit, brief)
    brief.appearance = _appearance(story, moment, people, first, names_of)
    brief.ages = _ages(story, when, people, first, names_of, unit)
    if clocks is not None:
        brief.movement = await _movement(
            story, scene, items, unit, clocks, workspace_id, project_id, people, first, names_of
        )
    if pictures is not None:
        brief.appearance.extend(
            await _pictures_seen(
                reader,
                pictures,
                workspace_id,
                project_id,
                scene,
                people,
                first,
                names_of,
                times,
                moment,
            )
        )


MAX_PLACES = 2_000


async def _movement(
    story: Story,
    scene: PlanItem,
    items: Sequence[PlanItem],
    unit: str,
    clocks: Any,
    workspace_id: str,
    project_id: str,
    people: Sequence[str],
    first: dict[str, str],
    names_of: dict[str, tuple[str, ...]],
) -> MovementBrief:
    """Places, journeys, and where the scene's characters are around its time and what
    keeps them, for travel read from the words (SCN-007, CON-004)."""
    from backend.domain.canon.plans import PlanKind
    from backend.services.continuity_service import how_long_of

    characters = [
        entity_id
        for entity_id in people
        if story.entities.get(entity_id) is not None
        and story.entities[entity_id].type == "Character"
    ]
    how_long, _ = await how_long_of(clocks, workspace_id, project_id)
    parts = StoryParts(
        entities=story.entities,
        facts=story.facts,
        events=story.events,
        scenes=[item for item in reading_order(items) if item.kind is PlanKind.SCENE],
        unit=unit,
        how_long=how_long,
    )
    # Reads the whole of story time: off the event loop for a large story.
    context = await asyncio.to_thread(movement_context, parts, scene.id, characters)
    place = next(iter(scene.refs_as("place")), None)
    # Keyed by entry, not by name: two characters who share a name stay two people.
    return MovementBrief(
        people={
            i: tuple(dict.fromkeys((first[i], *names_of.get(i, ()))))
            for i in characters
            if i in first
        },
        shown={i: first[i] for i in characters if i in first},
        places=[
            PlaceKnown(key, first.get(key, entity.name), names_of.get(key, (entity.name,)))
            for key, entity in list(context.places.items())[:MAX_PLACES]
        ],
        scene_place=place if place in context.places else None,
        same=dict(context.groups),
        travel=list(context.travel),
        elsewhere=[
            Elsewhere(
                item.entity_id,
                item.place_id,
                item.where,
                item.gap,
                item.gap_words,
                item.after,
            )
            for item in context.elsewhere
            if item.entity_id in first
        ],
        kept=[
            Kept(item.entity_id, item.kind, item.said, item.place_id)
            for item in context.kept
            if item.entity_id in first
        ],
    )


def _after(scene: PlanItem, story: Story, first: dict[str, str]) -> list[AfterShown]:
    names = {entity.id: entity.name for entity in story.entities.values()}
    found: list[AfterShown] = []
    for condition in scene_conditions(scene.details):
        if (
            condition.when != "after"
            or condition.entity_id is None
            or condition.check is None
            or condition.check.kind not in ("has", "lacks")
        ):
            continue
        pattern = condition.check.pattern
        other = (
            first.get(pattern.other_id)
            if pattern.other_id is not None
            else (pattern.value if pattern.value else None)
        )
        subject = first.get(condition.entity_id)
        if subject is None or not other:
            continue
        found.append(AfterShown(condition_reading(condition, names), (subject, other)))
    return found


def _previous(
    scene: PlanItem, items: Sequence[PlanItem], times: dict[str, Any], unit: str, story: Story
) -> TimeNeighbour | None:
    """The scene just before this one in reading order, when both are placed in time."""
    order = reading_order(items)
    index = next((i for i, item in enumerate(order) if item.id == scene.id), None)
    if not index:
        return None
    before = order[index - 1]
    this_when, then_when = scene_time(scene.details), scene_time(before.details)
    if this_when is None or then_when is None:
        return None
    lookup = anchor_lookup(times)
    this_at = resolve(this_when, lookup).interval
    then_at = resolve(then_when, lookup).interval
    if this_at is None or then_at is None:
        return None
    if any(math.isinf(value) for value in (this_at.lo, this_at.hi, then_at.lo, then_at.hi)):
        return None
    whole = counts_whole_units(unit)
    days = days_between((then_at.lo, then_at.hi), (this_at.lo, this_at.hi), unit, whole=whole)
    # In the story's own units, whatever they are: "chapter 3" after "chapter 4" is
    # before it even when a chapter has no length in days.
    reach = 1.0 if whole else 0.0
    then_hi = then_at.hi + (reach if whole and float(then_at.hi).is_integer() else 0)
    this_hi = this_at.hi + (reach if whole and float(this_at.hi).is_integer() else 0)
    apart = (this_at.lo - then_hi, this_hi - then_at.lo)
    event_names = {event.id: event.name for event in story.events}
    if days is None:
        return TimeNeighbour(
            title=before.title,
            days=None,
            this_when=describe(this_when, unit=unit, event_name=event_names.get),
            then_when=describe(then_when, unit=unit, event_name=event_names.get),
            apart=apart,
        )
    same_day: bool | None = None
    word = unit.strip().lower().removesuffix("s")
    if (
        word == "day"
        and whole
        and isinstance(this_when, At)
        and isinstance(then_when, At)
        and float(this_when.at).is_integer()
        and float(then_when.at).is_integer()
    ):
        same_day = this_when.at == then_when.at
    return TimeNeighbour(
        title=before.title,
        days=days,
        same_day=same_day,
        this_when=describe(this_when, unit=unit, event_name=event_names.get),
        then_when=describe(then_when, unit=unit, event_name=event_names.get),
        apart=apart,
    )


def _holdings(
    story: Story,
    moment: Interval | None,
    present: set[str],
    first: dict[str, str],
    names_of: dict[str, tuple[str, ...]],
    brief: DraftBrief,
) -> list[Holding]:
    """Things in someone's hands at the scene's time when that someone is not in it, and
    things used up by then. A thing with two holders then is left to the continuity check."""
    times = event_times(story.events)
    holders: dict[str, list[str]] = {}
    gone: dict[str, str] = {}
    for fact in story.facts:
        if fact.closed_revision is not None:
            continue
        subject = fact.proposition.subject_id
        predicate = fact.proposition.predicate
        target = getattr(fact.proposition.object, "entity_id", None)
        if predicate in IN_HAND and target is not None:
            if _true_then(fact_span(fact, times), moment):
                holders.setdefault(target, []).append(subject)
        elif predicate == STATUS_PREDICATE:
            value = str(getattr(fact.proposition.object, "value", "") or "").strip().lower()
            if value in GONE_WORDS and _true_then(fact_span(fact, times), moment):
                gone[subject] = f"its status is “{value}”"
    found: list[Holding] = []
    for thing in sorted({*holders, *gone}):
        entity = story.entities.get(thing)
        if entity is None or entity.type != "Object" or thing not in first:
            continue
        if thing in gone:
            found.append(
                Holding(first[thing], names_of.get(thing, (first[thing],)), gone=gone[thing])
            )
        else:
            who = holders[thing]
            if len(set(who)) != 1 or who[0] in present or who[0] not in first:
                continue
            found.append(
                Holding(
                    first[thing],
                    names_of.get(thing, (first[thing],)),
                    holder=first[who[0]],
                    holder_names=names_of.get(who[0], (first[who[0]],)),
                )
            )
        brief.ids.setdefault(first[thing], thing)
        if len(found) >= MAX_HOLDINGS:
            break
    return found


def _number(value: Any) -> float | None:
    """A finite number from a fact's value (an integer, a decimal or a quantity), or None."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal | str):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# Kinds of fact that say how many of a thing there are (SCN-007), as the author names
# them: "has quantity", "count", "amount", "how many".
QUANTITY_KINDS = frozenset(
    {
        "HAS_QUANTITY",
        "QUANTITY",
        "HAS_COUNT",
        "COUNT",
        "HAS_AMOUNT",
        "AMOUNT",
        "HAS_SUPPLY",
        "SUPPLY",
        "HOW_MANY",
        "NUMBER_OF",
    }
)
_COUNTABLE = frozenset({"Object", "Resource"})


def _supplies(
    story: Story,
    moment: Interval | None,
    first: dict[str, str],
    names_of: dict[str, tuple[str, ...]],
    unit: str,
    brief: DraftBrief,
) -> list[Supply]:
    """How many of each thing there are at the scene's time: a fact of a kind that says
    so, with a number, certainly true then. Two such facts true at once say nothing."""
    times = event_times(story.events)
    event_names = {event.id: event.name for event in story.events}
    amounts: dict[str, list[tuple[float, Any]]] = {}
    for fact in story.facts:
        subject = fact.proposition.subject_id
        entity = story.entities.get(subject)
        if (
            fact.closed_revision is not None
            or fact.proposition.predicate not in QUANTITY_KINDS
            or entity is None
            or entity.type not in _COUNTABLE
            or subject not in first
            or not _true_then(fact_span(fact, times), moment)
        ):
            continue
        value = _number(getattr(fact.proposition.object, "value", None))
        if value is None or value < 0:
            continue
        amounts.setdefault(subject, []).append((value, fact))
    found: list[Supply] = []
    for thing, rows in sorted(amounts.items()):
        if len({value for value, _fact in rows}) != 1:
            continue
        value, fact = rows[0]
        since = (
            f" (from {describe(fact.starts, unit=unit, event_name=event_names.get)})"
            if fact.starts is not None
            else ""
        )
        found.append(
            Supply(
                first[thing],
                names_of.get(thing, (first[thing],)),
                value,
                source=f"the story bible{since}",
                key=fact.id,
            )
        )
        brief.ids.setdefault(first[thing], thing)
        if len(found) >= MAX_HOLDINGS:
            break
    return found


def _appearance(
    story: Story,
    moment: Interval | None,
    people: Sequence[str],
    first: dict[str, str],
    names_of: dict[str, tuple[str, ...]],
) -> list[AppearanceDetail]:
    """Accepted appearance details of the scene's people that may hold at its time."""
    times = event_times(story.events)
    wanted = set(people)
    found: list[AppearanceDetail] = []
    counted: dict[str, int] = {}
    for fact in story.facts:
        subject = fact.proposition.subject_id
        if (
            fact.closed_revision is not None
            or fact.proposition.predicate != APPEARANCE_PREDICATE
            or subject not in wanted
            or counted.get(subject, 0) >= MAX_DETAILS_PER_PERSON
            or not _possible_then(fact_span(fact, times), moment)
        ):
            continue
        words = detail_words(getattr(fact.proposition.object, "value", None))
        if not words.strip():
            continue
        counted[subject] = counted.get(subject, 0) + 1
        found.append(
            AppearanceDetail(
                who=first[subject],
                names=names_of.get(subject, (first[subject],)),
                words=words,
                source="the story bible",
                key=fact.id,
            )
        )
    return found


# Just short of the next whole unit: "year 5" is any time in year 5.
_WITHIN = 1e-6


def _widened(interval: Interval | None, when: Any, whole: bool) -> Interval | None:
    if interval is None or math.isinf(interval.lo) or math.isinf(interval.hi):
        return None
    hi = interval.hi
    if (
        whole
        and float(hi).is_integer()
        and (isinstance(when, At) or getattr(when, "latest", None) is not None)
    ):
        hi = hi + 1 - _WITHIN
    return Interval(interval.lo, hi)


def _ages(
    story: Story,
    when: Any,
    people: Sequence[str],
    first: dict[str, str],
    names_of: dict[str, tuple[str, ...]],
    unit: str,
) -> list[AgeKnown]:
    """How old each person in the scene can be at its time, in a story counted in years:
    from a birth placed in story time, or an age the author gave from a time (CON-004).
    Nothing when the story is not counted in years or the scene has no time."""
    if " ".join(unit.lower().split()).removesuffix("s") != "year" or when is None:
        return []
    times = event_times(story.events)
    lookup = anchor_lookup(times)
    whole = counts_whole_units(unit)
    scene_at = _widened(resolve(when, lookup).interval, when, whole)
    if scene_at is None:
        return []
    names = {event.id: event.name for event in story.events}
    scene_words = describe(when, unit=unit, event_name=names.get)
    wanted = set(people)
    found: list[AgeKnown] = []

    def add(subject: str, born_lo: float, born_hi: float, because: str, key: str) -> None:
        lo = math.floor(scene_at.lo - born_hi + 1e-9)
        hi = math.floor(scene_at.hi - born_lo + 1e-9)
        if hi < 0 or lo > hi:
            return
        found.append(
            AgeKnown(
                who=first[subject],
                names=names_of.get(subject, (first[subject],)),
                lo=max(lo, 0),
                hi=hi,
                because=because,
                key=key,
            )
        )

    for event in story.events:
        kind = " ".join(str(event.kind).lower().split())
        if kind not in BIRTH_KINDS:
            continue
        characters = [
            item
            for item in event.participants
            if story.entities.get(item.entity_id) is not None
            and story.entities[item.entity_id].type == "Character"
        ]
        whose = [item.entity_id for item in characters if str(item.role).lower() in BIRTH_ROLES]
        if not whose and len(characters) == 1 and kind in PLAIN_LIFE_KINDS:
            whose = [characters[0].entity_id]
        born = _widened(times[event.id].interval, event.when, whole) if event.id in times else None
        if born is None:
            continue
        for subject in dict.fromkeys(whose):
            if subject in wanted and subject in first:
                add(
                    subject,
                    born.lo,
                    born.hi,
                    f"“{event.name}” is {describe(event.when, unit=unit, event_name=names.get)} "
                    f"and this scene is {scene_words}",
                    event.id,
                )
    for fact in story.facts:
        subject = fact.proposition.subject_id
        if (
            fact.closed_revision is not None
            or fact.proposition.predicate not in AGE_KINDS
            or fact.starts is None
            or subject not in wanted
            or subject not in first
        ):
            continue
        obj = fact.proposition.object
        said_unit = getattr(obj, "unit", None)
        if said_unit is not None and str(said_unit).lower().removesuffix("s") != "year":
            continue
        value = _number(getattr(obj, "value", None))
        if value is None or value < 0 or not value.is_integer():
            continue
        start = _widened(resolve(fact.starts, lookup).interval, fact.starts, whole)
        if start is None:
            continue
        # Aged v at some time in [start]: born after start.lo - v - 1, by start.hi - v.
        add(
            subject,
            start.lo - value - 1 + _WITHIN,
            start.hi - value,
            f"the story bible gives {first[subject]}'s age as {int(value)} from "
            f"{describe(fact.starts, unit=unit, event_name=names.get)} and this scene is "
            f"{scene_words}",
            fact.id,
        )
    return found


async def _pictures_seen(
    reader: Any,
    pictures: Any,
    workspace_id: str,
    project_id: str,
    scene: PlanItem,
    people: Sequence[str],
    first: dict[str, str],
    names_of: dict[str, tuple[str, ...]],
    times: dict[str, Any],
    moment: Interval | None,
) -> list[AppearanceDetail]:
    """Appearance details a model saw in a picture of the scene or of its people, that the
    author accepted or corrected (CON-014), each naming its picture. A picture that only
    applies from a later story time is left out."""
    store = getattr(pictures, "_store", None)
    if store is None or not people:
        return []
    links = await reader.list_links(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_LINKS
    )
    wanted = set(people)
    lookup = anchor_lookup(times)
    # Picture -> the versions its links show (None: its newest), as the context does.
    versions: dict[str, set[str | None]] = {}
    for link in links:
        if not link.is_active:
            continue
        on_scene = link.target_record_type == "plan_item" and link.target_record_id == scene.id
        of_person = link.target_record_type != "plan_item" and link.target_record_id in wanted
        if not (on_scene or of_person):
            continue
        if moment is not None and link_applies_at(link, moment, lookup) is Truth.FALSE:
            continue
        versions.setdefault(link.asset_id, set()).add(
            link.asset_version_id if link.shows_version else None
        )
    if not versions:
        return []
    # AST-001: a picture whose links all show one version is read at that version, so the
    # details checked against the prose are the ones that version shows.
    pinned = {
        asset_id: next(iter(shown_versions))
        for asset_id, shown_versions in versions.items()
        if len(shown_versions) == 1 and None not in shown_versions
    }
    shown = await pictures.for_context(workspace_id, project_id, list(versions), pinned=pinned)
    observations = await store.for_assets(
        workspace_id=workspace_id, project_id=project_id, asset_ids=list(shown)
    )
    found: list[AppearanceDetail] = []
    for observation in observations:
        picture = shown.get(observation.asset_id)
        subject = observation.subject_record_id
        if (
            picture is None
            or observation.kind is not ObservationKind.APPEARANCE
            or not observation.accepted
            or observation.asset_version_id != picture.version.id
            or subject not in wanted
        ):
            continue
        found.append(
            AppearanceDetail(
                who=first[subject],
                names=names_of.get(subject, (first[subject],)),
                words=observation.words,
                source=f"the picture “{picture.asset.name}”",
                key=observation.id,
            )
        )
    return found


__all__ = ["GONE_WORDS", "QUANTITY_KINDS", "add_prose_signals"]
