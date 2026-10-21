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

import math
from collections.abc import Sequence
from typing import Any

from backend.domain.appearance import AppearanceDetail, detail_words
from backend.domain.canon.plans import PlanItem
from backend.domain.continuity import POSSESSION_KINDS, counts_whole_units
from backend.domain.drafting import AfterShown, DraftBrief, Holding, TimeNeighbour, days_between
from backend.domain.knowledge import reading_order, scene_time
from backend.domain.media_observations import ObservationKind
from backend.domain.scene_conditions import condition_reading, scene_conditions
from backend.domain.worldtime import (
    At,
    Interval,
    Truth,
    describe,
    parse_world_time,
    resolve,
    span_of,
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
) -> None:
    """Fill the brief's fields for the checks against the prose, in place."""
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
    brief.appearance = _appearance(story, moment, people, first, names_of)
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
    if days is None:
        return None
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
    event_names = {event.id: event.name for event in story.events}
    return TimeNeighbour(
        title=before.title,
        days=days,
        same_day=same_day,
        this_when=describe(this_when, unit=unit, event_name=event_names.get),
        then_when=describe(then_when, unit=unit, event_name=event_names.get),
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
    asset_ids: list[str] = []
    for link in links:
        if not link.is_active:
            continue
        on_scene = link.target_record_type == "plan_item" and link.target_record_id == scene.id
        of_person = link.target_record_type != "plan_item" and link.target_record_id in wanted
        if not (on_scene or of_person):
            continue
        if link.applies_from is not None and moment is not None:
            start = parse_world_time(link.applies_from)
            if (
                start is not None
                and true_at(span_of(resolve(start, lookup), None), moment) is Truth.FALSE
            ):
                continue
        asset_ids.append(link.asset_id)
    asset_ids = list(dict.fromkeys(asset_ids))
    if not asset_ids:
        return []
    shown = await pictures.for_context(workspace_id, project_id, asset_ids)
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


__all__ = ["GONE_WORDS", "add_prose_signals"]
