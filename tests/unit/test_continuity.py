"""Continuity across story time and place, worked out on read (CON-003, CON-004)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.domain.canon.assertions import Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.plans import PlanItem, PlanKind, PlanRef
from backend.domain.canon.propositions import Proposition
from backend.domain.continuity import (
    COVERAGE,
    HowLong,
    Lasting,
    StoryParts,
    TravelTime,
    continuity_findings,
    for_scene,
    known_records,
    read_how_long,
)
from backend.domain.knowledge import with_scene_time
from backend.domain.worldtime import At, Between, Relative

pytestmark = pytest.mark.unit


def _entity(id_: str, type_: str, name: str | None = None) -> CanonEntity:
    return CanonEntity(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        type=type_,
        name=name or id_.title(),
        schema_version_id="s",
        created_revision=1,
    )


def _fact(id_: str, subject: str, predicate: str, obj: Any, **fields: Any) -> Fact:
    if isinstance(obj, str):
        obj = {"kind": "entity_ref", "entity_id": obj, "project_id": "p"}
    return Fact(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        proposition=Proposition(subject_id=subject, predicate=predicate, object=obj),
        provenance_id="prov",
        created_revision=1,
        **fields,
    )


def _event(id_: str, when: Any, *, kind: str = "happening", who=(), where=(), name=None) -> Event:
    return Event(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        kind=kind,
        name=name or id_.replace("_", " ").title(),
        participants=[
            EventParticipant(entity_id=item, role="participant")
            if isinstance(item, str)
            else EventParticipant(entity_id=item[0], role=item[1])
            for item in who
        ],
        location_ids=list(where),
        when=when,
        provenance_id="prov",
        created_revision=1,
    )


def _scene(id_: str, when: Any, *, place: str | None = None, who=(), pov=None) -> PlanItem:
    refs = [PlanRef(entity_id=item, role="participant") for item in who]
    if place:
        refs.append(PlanRef(entity_id=place, role="place"))
    if pov:
        refs.append(PlanRef(entity_id=pov, role="point_of_view"))
    return PlanItem(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        kind=PlanKind.SCENE,
        title=id_.replace("_", " ").title(),
        refs=refs,
        details=with_scene_time({}, when) if when is not None else {},
        provenance_id="prov",
        created_revision=1,
    )


PEOPLE = [
    _entity("mara", "Character"),
    _entity("ines", "Character"),
    _entity("harbor", "Location", "Lantern Harbor"),
    _entity("keep", "Location", "Saltkeep"),
    _entity("tavern", "Location", "The Gull"),
    _entity("sword", "Object", "the sword"),
    _entity("guild", "Faction", "the Guild"),
]


def _run(*, facts=(), events=(), scenes=(), unit="year", how_long=None, more=()) -> list:
    return continuity_findings(
        StoryParts(
            entities={item.id: item for item in [*PEOPLE, *more]},
            facts=list(facts),
            events=list(events),
            scenes=list(scenes),
            unit=unit,
            how_long=how_long or HowLong(),
        )
    )


def _codes(findings) -> list[str]:
    return [item.code for item in findings]


# ------------------------------------------------------------------ places and journeys


def test_one_character_in_two_scenes_at_two_places_at_the_same_moment() -> None:
    found = _run(
        scenes=[
            _scene("low_tide", At(at=3), place="harbor", who=["mara"]),
            _scene("the_vault", At(at=3), place="keep", pov="mara"),
        ],
        unit="hour",
    )
    (finding,) = found
    assert finding.code == "two_places_at_once" and finding.severity == "warning"
    assert "Mara is at Lantern Harbor" in finding.message and "Saltkeep" in finding.message
    assert set(finding.scene_ids) == {"low_tide", "the_vault"}
    assert {record.kind for record in finding.records} == {"entry", "scene"}
    assert for_scene(found, "low_tide") == [finding]


def test_loose_times_never_warn_and_untimed_scenes_are_not_checked() -> None:
    assert not _run(
        scenes=[
            _scene("a", Between(earliest=2, latest=4), place="harbor", who=["mara"]),
            _scene("b", At(at=3), place="keep", who=["mara"]),
            _scene("c", None, place="tavern", who=["mara"]),
        ]
    )


def test_a_scene_inside_a_stay_elsewhere_is_two_places_at_once() -> None:
    stay = _fact("f_at", "mara", "LOCATED_AT", "keep", starts=At(at=1), ends=At(at=10))
    found = _run(facts=[stay], scenes=[_scene("s", At(at=5), place="harbor", who=["mara"])])
    assert _codes(found) == ["two_places_at_once"]
    assert any(record.kind == "fact" and record.entry_id == "mara" for record in found[0].records)
    # A fact with no time says nothing about when, so it is not compared.
    timeless = _fact("f_at", "mara", "LOCATED_AT", "keep")
    assert not _run(facts=[timeless], scenes=[_scene("s", At(at=5), place="harbor", who=["mara"])])


def test_a_place_inside_another_never_counts_as_two_places() -> None:
    inside = HowLong(travel=[TravelTime(from_id="tavern", to_id="harbor", amount=0)])
    scenes = [
        _scene("a", At(at=3), place="harbor", who=["mara"]),
        _scene("b", At(at=3), place="tavern", who=["mara"]),
    ]
    assert _codes(_run(scenes=scenes, unit="hour")) == ["two_places_at_once"]
    assert not _run(scenes=scenes, how_long=inside, unit="hour")


def test_travel_needs_the_authors_time_and_is_never_guessed() -> None:
    scenes = [
        _scene("leaving", At(at=1), place="harbor", who=["mara"]),
        _scene("arriving", At(at=2), place="keep", who=["mara"]),
    ]
    assert not _run(scenes=scenes)
    slow = HowLong(travel=[TravelTime(from_id="keep", to_id="harbor", amount=3)])
    (finding,) = _run(scenes=scenes, how_long=slow)
    assert finding.code == "too_little_travel_time" and finding.check == "travel"
    # Year 1 and year 2 are whole years: at most two years apart.
    assert "at most 2 years later" in finding.message and "takes 3 years" in finding.message
    fast = HowLong(travel=[TravelTime(from_id="harbor", to_id="keep", amount=1)])
    assert not _run(scenes=scenes, how_long=fast)


def test_travel_between_two_stays_and_between_a_stay_and_a_scene() -> None:
    there = _fact("f1", "mara", "LOCATED_AT", "keep", starts=At(at=0), ends=At(at=5))
    then = _fact("f2", "mara", "LOCATED_AT", "harbor", starts=At(at=5), ends=At(at=9))
    road = HowLong(travel=[TravelTime(from_id="keep", to_id="harbor", amount=1)])
    hours = {"how_long": road, "unit": "hour"}
    assert _codes(_run(facts=[there, then], **hours)) == ["too_little_travel_time"]
    scene = _scene("s", Between(earliest=5, latest=5.5), place="harbor", who=["mara"])
    assert _codes(_run(facts=[there], scenes=[scene], **hours)) == ["too_little_travel_time"]
    later = _scene("s", Between(earliest=5, latest=7), place="harbor", who=["mara"])
    assert not _run(facts=[there], scenes=[later], **hours)
    # In whole years, leaving some time in year 5 and arriving in year 5 may be a year apart.
    assert not _run(facts=[there, then], how_long=road)


def test_events_with_one_place_count_and_claims_do_not() -> None:
    fight = _event("fight", At(at=4), who=["mara"], where=["keep"])
    scene = _scene("s", At(at=4), place="harbor", who=["mara"])
    assert _codes(_run(events=[fight], scenes=[scene], unit="hour")) == ["two_places_at_once"]
    claim = _event("said", At(at=4), kind="claim", who=["mara"], where=["keep"])
    assert not _run(events=[claim], scenes=[scene], unit="hour")
    # A place among the people in it is the event's place, as the timeline adds them.
    party = _event("party", At(at=4), who=["mara", "keep"])
    assert _codes(_run(events=[party], scenes=[scene], unit="hour")) == ["two_places_at_once"]


# ------------------------------------------------------------------ ages and lengths


def test_age_from_a_birth_against_a_stated_age() -> None:
    born = _event("mara_is_born", At(at=0), kind="birth", who=["mara"])
    right = _fact("age", "mara", "AGE", {"kind": "integer", "value": 10}, starts=At(at=10))
    assert not _run(events=[born], facts=[right])
    # Born late in year 0, early in year 11 she is still ten.
    later = _fact("age", "mara", "AGE", {"kind": "integer", "value": 10}, starts=At(at=11))
    assert not _run(events=[born], facts=[later])
    wrong = _fact("age", "mara", "AGE", {"kind": "integer", "value": 10}, starts=At(at=12))
    (finding,) = _run(events=[born], facts=[wrong])
    assert finding.code == "age_against_birth" and "cannot be 10" in finding.message
    # Not counted in years, and the age gives no unit: not checked.
    assert not _run(events=[born], facts=[wrong], unit="day")
    in_days = _fact(
        "age", "mara", "AGE", {"kind": "quantity", "value": "3", "unit": "days"}, starts=At(at=9)
    )
    assert _codes(_run(events=[born], facts=[in_days], unit="day")) == ["age_against_birth"]


def test_two_stated_ages_that_cannot_both_be_true() -> None:
    young = _fact("a1", "mara", "HAS_AGE", {"kind": "integer", "value": 10}, starts=At(at=5))
    old = _fact("a2", "mara", "HAS_AGE", {"kind": "integer", "value": 30}, starts=At(at=10))
    assert _codes(_run(facts=[young, old])) == ["ages_disagree"]


def test_durations_against_placed_times() -> None:
    stay = _fact("f", "mara", "LOCATED_AT", "keep", starts=At(at=1), ends=At(at=2))
    long = HowLong(lasting=[Lasting(subject_kind="fact", subject_id="f", amount=4)])
    (finding,) = _run(facts=[stay], how_long=long)
    assert finding.code == "span_wrong_length" and "at most 2 years long" in finding.message
    short = HowLong(lasting=[Lasting(subject_kind="fact", subject_id="f", amount=0.5)])
    assert _codes(_run(facts=[stay], how_long=short, unit="hour")) == ["span_wrong_length"]
    # From some time in year 1 to some time in year 2 may be half a year, or two.
    assert not _run(facts=[stay], how_long=short)
    loose = HowLong(
        lasting=[Lasting(subject_kind="fact", subject_id="f", amount=3, give_or_take=1)]
    )
    assert not _run(facts=[stay], how_long=loose)


# ------------------------------------------------------------------ places' lives


def test_scenes_and_events_at_a_place_before_it_is_built_or_after_it_is_gone() -> None:
    built = _event("keep_built", At(at=10), kind="built", who=["keep"])
    burned = _event("keep_razed", At(at=50), kind="razed", where=["keep"])
    early = _scene("early", At(at=5), place="keep", who=["mara"])
    late = _scene("late", At(at=60), place="keep", who=["mara"])
    fine = _scene("fine", At(at=20), place="keep", who=["mara"])
    found = _run(events=[built, burned], scenes=[early, late, fine])
    assert sorted(_codes(found)) == ["place_gone", "place_not_yet"]
    assert {scene for item in found for scene in item.scene_ids} == {"early", "late"}
    stay = _fact("f", "mara", "LOCATED_AT", "keep", starts=At(at=2))
    assert "place_not_yet" in _codes(_run(events=[built], facts=[stay]))


# ------------------------------------------------------------------ possession


def test_one_object_held_by_two() -> None:
    hers = _fact("f1", "mara", "OWNS", "sword")
    theirs = _fact("f2", "ines", "OWNS", "sword")
    (note,) = _run(facts=[hers, theirs])
    assert note.code == "two_holders" and note.severity == "note"
    timed = _fact("f1", "mara", "CARRIES", "sword", starts=At(at=1), ends=At(at=5))
    other = _fact("f2", "ines", "CARRIES", "sword", starts=At(at=3), ends=At(at=8))
    (warning,) = _run(facts=[timed, other])
    assert warning.code == "two_holders_at_once" and warning.severity == "warning"
    handed_on = _fact("f2", "ines", "CARRIES", "sword", starts=At(at=5))
    assert not _run(facts=[timed, handed_on])


def test_an_object_in_a_scene_without_whoever_has_it_then() -> None:
    hers = _fact("f1", "mara", "OWNS", "sword")
    scene = _scene("duel", At(at=3), place="keep", who=["ines", "sword"])
    (note,) = _run(facts=[hers], scenes=[scene])
    assert note.code == "object_away_from_holder" and note.scene_ids == ("duel",)
    with_her = _scene("duel", At(at=3), place="keep", who=["ines", "sword", "mara"])
    assert not _run(facts=[hers], scenes=[with_her])


# ------------------------------------------------------------------ life and condition


def test_a_participant_after_their_recorded_death() -> None:
    dies = _event("mara_dies", At(at=10), kind="death", who=["mara"])
    after = _scene("after", At(at=12), place="keep", who=["mara"])
    before = _scene("before", At(at=8), place="keep", who=["mara"])
    found = _run(events=[dies], scenes=[after, before])
    (finding,) = found
    assert finding.code == "after_death" and finding.scene_ids == ("after",)
    assert "after their death: “Mara Dies” (year 10)" in finding.message
    # A death with two people in it and no role saying who died is not guessed.
    duel = _event("duel", At(at=10), kind="death", who=["mara", "ines"])
    assert not _run(events=[duel], scenes=[after])
    named = _event("duel", At(at=10), kind="killing", who=[("mara", "victim"), "ines"])
    assert _codes(_run(events=[named], scenes=[after])) == ["after_death"]


def test_before_birth_a_death_before_a_birth_and_two_deaths() -> None:
    born = _event("born", At(at=20), kind="born", who=["mara"])
    scene = _scene("s", At(at=5), place="keep", who=["mara"])
    assert _codes(_run(events=[born], scenes=[scene])) == ["before_birth"]
    dies = _event("dies", At(at=10), kind="death", who=["mara"])
    assert "dies_before_birth" in _codes(_run(events=[born, dies]))
    again = _event("dies_again", At(at=30), kind="death", who=["mara"])
    assert "dies_twice" in _codes(_run(events=[dies, again]))
    walk = _event("walk", At(at=15), who=["mara"])
    assert "after_death" in _codes(_run(events=[dies, walk]))


def test_a_status_says_dead_or_injured() -> None:
    enum = {"kind": "enum", "value": "injured", "allowed": ["injured", "well"]}
    hurt = _fact("f", "mara", "HAS_STATUS", enum)
    scene = _scene("s", At(at=3), place="keep", who=["mara"])
    (note,) = _run(facts=[hurt], scenes=[scene])
    assert note.code == "condition_no_time" and note.severity == "note"
    timed = _fact("f", "mara", "CONDITION", {"kind": "string", "value": "Wounded"}, starts=At(at=1))
    assert _codes(_run(facts=[timed], scenes=[scene])) == ["condition_then"]
    dead = _fact("f", "mara", "CONDITION", {"kind": "string", "value": "dead"}, starts=At(at=1))
    assert _codes(_run(facts=[dead], scenes=[scene])) == ["after_death"]


# ------------------------------------------------------------------ changes off the page


def test_relationships_and_goals_that_change_with_nothing_on_the_page() -> None:
    joins = _fact("f", "mara", "MEMBER_OF", "guild", starts=At(at=5))
    (note,) = _run(facts=[joins])
    assert note.code == "relationship_changes_off_page" and note.severity == "note"
    shown = _scene("joining", At(at=5), place="keep", who=["mara"])
    assert not _run(facts=[joins], scenes=[shown])
    at_event = _event("oath", At(at=5))
    anchored = _fact(
        "f", "mara", "MEMBER_OF", "guild", starts=Relative(anchor_event_id="oath", offset=0)
    )
    assert not _run(facts=[anchored], events=[at_event])
    goal = _entity("revenge", "Goal")
    wants = _fact("g", "mara", "PURSUES", "revenge", ends=At(at=9))
    assert _codes(_run(facts=[wants], more=[goal])) == ["goal_changes_off_page"]
    # A fact true from the start of the story has not changed.
    assert not _run(facts=[_fact("f", "mara", "KNOWS", "ines")])


# ------------------------------------------------------------------ the table


def test_the_table_refuses_repeats_and_reads_back_empty_when_broken() -> None:
    with pytest.raises(ValidationError):
        HowLong(
            travel=[
                TravelTime(from_id="a", to_id="b", amount=1),
                TravelTime(from_id="b", to_id="a", amount=2),
            ]
        )
    with pytest.raises(ValidationError):
        TravelTime(from_id="a", to_id="a", amount=1)
    assert read_how_long({"travel": "nonsense"}) == HowLong()
    assert read_how_long(None) == HowLong()
    places = {item.id: item for item in PEOPLE}
    table = HowLong(
        travel=[TravelTime(from_id="harbor", to_id="mara", amount=1)],
        lasting=[Lasting(subject_kind="event", subject_id="gone", amount=1)],
    )
    assert len(known_records(table, places, [], [])) == 2


def test_every_check_says_what_it_covers() -> None:
    checks = {item.check for item in COVERAGE}
    assert checks == {
        "simultaneity",
        "travel",
        "age",
        "duration",
        "place",
        "possession",
        "life",
        "state",
        "lifecycle",
        "change",
    }


# ------------------------------------------------------------------ review fixes


def test_a_stay_whose_start_cannot_be_placed_is_never_certain() -> None:
    # Starts at an event that is not there, or one with no time: some time we cannot place.
    scene = _scene("s", At(at=5), place="harbor", who=["mara"])
    gone = _fact(
        "f_at",
        "mara",
        "LOCATED_AT",
        "keep",
        starts=Relative(anchor_event_id="nope"),
        ends=At(at=10),
    )
    assert not _run(facts=[gone], scenes=[scene])
    untimed = _event("oath", None)
    loose = _fact(
        "f_at",
        "mara",
        "LOCATED_AT",
        "keep",
        starts=Relative(anchor_event_id="oath"),
        ends=At(at=10),
    )
    assert not _run(facts=[loose], events=[untimed], scenes=[scene])
    # An end that cannot be placed says nothing certain either.
    open_end = _fact(
        "f_at", "mara", "LOCATED_AT", "keep", starts=At(at=1), ends=Relative(anchor_event_id="nope")
    )
    assert not _run(facts=[open_end], scenes=[scene])


def test_possession_with_a_time_that_cannot_be_placed_is_not_the_whole_story() -> None:
    timed = _fact("f1", "mara", "CARRIES", "sword", starts=At(at=1), ends=At(at=5))
    unplaced = _fact("f2", "ines", "CARRIES", "sword", starts=Relative(anchor_event_id="nope"))
    assert not _run(facts=[timed, unplaced])
    both = [
        _fact(
            "f1", "mara", "CARRIES", "sword", starts=Relative(anchor_event_id="nope"), ends=At(at=5)
        ),
        _fact(
            "f2", "ines", "CARRIES", "sword", starts=Relative(anchor_event_id="nope"), ends=At(at=8)
        ),
    ]
    assert not _run(facts=both)
    # Only a fact with no times at all is true throughout the story.
    always = _fact("f2", "ines", "CARRIES", "sword")
    assert _codes(_run(facts=[timed, always])) == ["two_holders_at_once"]
    # Nor does a holder from an unplaceable time "have it then" in a scene.
    hers = _fact("f1", "mara", "OWNS", "sword", starts=Relative(anchor_event_id="nope"))
    scene = _scene("duel", At(at=3), place="keep", who=["ines", "sword"])
    assert not _run(facts=[hers], scenes=[scene])


def test_a_condition_from_a_time_that_cannot_be_placed() -> None:
    scene = _scene("s", At(at=3), place="keep", who=["mara"])
    wounded = {"kind": "string", "value": "wounded"}
    until = _fact(
        "f", "mara", "CONDITION", wounded, starts=Relative(anchor_event_id="nope"), ends=At(at=10)
    )
    assert "condition_then" not in _codes(_run(facts=[until], scenes=[scene]))
    unplaced = _fact("f", "mara", "CONDITION", wounded, starts=Relative(anchor_event_id="nope"))
    (note,) = _run(facts=[unplaced], scenes=[scene])
    assert note.code == "condition_no_time" and "cannot be placed" in note.message


def test_a_killing_with_one_person_in_it_is_not_their_death() -> None:
    after = _scene("after", At(at=12), place="keep", who=["mara"])
    for kind in ("killing", "murder", "execution", "killed"):
        # "Mara killed the guard": she did it, she did not die.
        did_it = _event("deed", At(at=10), kind=kind, who=["mara"])
        assert not _run(events=[did_it], scenes=[after]), kind
        victim = _event("deed", At(at=10), kind=kind, who=[("mara", "victim")])
        assert _codes(_run(events=[victim], scenes=[after])) == ["after_death"], kind
    for kind in ("death", "died", "dies"):
        plain = _event("end", At(at=10), kind=kind, who=["mara"])
        assert _codes(_run(events=[plain], scenes=[after])) == ["after_death"], kind


def test_a_death_by_another_word_is_read_as_a_death() -> None:
    after = _scene("after", At(at=12), place="keep", who=["mara"])
    for kind in ("drowned", "perished", "slain", "murdered", "executed", "assassinated"):
        died = _event("end", At(at=10), kind=kind, who=[("mara", "died")])
        assert _codes(_run(events=[died], scenes=[after])) == ["after_death"], kind
        victim = _event("end", At(at=10), kind=kind, who=[("mara", "victim"), "ines"])
        assert _codes(_run(events=[victim], scenes=[after])) == ["after_death"], kind
    # Drowned, perished and slain only ever happen to the one in them.
    for kind in ("drowned", "perished", "slain"):
        alone = _event("end", At(at=10), kind=kind, who=["mara"])
        assert _codes(_run(events=[alone], scenes=[after])) == ["after_death"], kind
    # "Mara murdered the guard" is written with the same word as "Mara was murdered":
    # with no part saying who died, the one in it may be the one who did it.
    for kind in ("murdered", "executed", "assassinated", "murder", "assassination"):
        alone = _event("deed", At(at=10), kind=kind, who=["mara"])
        assert not _run(events=[alone], scenes=[after]), kind
    # A part saying they died is a death whatever the event is called.
    for role in ("died", "dies", "deceased"):
        flood = _event("flood", At(at=10), kind="flood", who=[("mara", role), "ines"])
        assert _codes(_run(events=[flood], scenes=[after])) == ["after_death"], role
    survived = _event("flood", At(at=10), kind="flood", who=[("mara", "victim")])
    assert not _run(events=[survived], scenes=[after])
    # A status written with one of these words is a death too.
    drowned = _fact("f", "mara", "STATUS", {"kind": "string", "value": "drowned"}, starts=At(at=10))
    assert _codes(_run(facts=[drowned], scenes=[after])) == ["after_death"]


def test_a_whole_year_is_not_one_moment() -> None:
    scenes = [
        _scene("a", At(at=5), place="harbor", who=["mara"]),
        _scene("b", At(at=5), place="keep", who=["mara"]),
    ]
    # In years, "year 5" is any time that year; in hours, hour 5 is one moment.
    assert not _run(scenes=scenes, unit="year")
    assert not _run(scenes=scenes, unit="day")
    assert _codes(_run(scenes=scenes, unit="hour")) == ["two_places_at_once"]
    journey = [
        _scene("leaving", At(at=5), place="harbor", who=["mara"]),
        _scene("arriving", At(at=6), place="keep", who=["mara"]),
    ]
    two = HowLong(travel=[TravelTime(from_id="harbor", to_id="keep", amount=2)])
    assert not _run(scenes=journey, how_long=two)
    three = HowLong(travel=[TravelTime(from_id="harbor", to_id="keep", amount=3)])
    (finding,) = _run(scenes=journey, how_long=three)
    assert "at most 2 years later" in finding.message
    # A whole year before a birth is still before it.
    born = _event("born", At(at=5), kind="birth", who=["mara"])
    early = _scene("early", At(at=4), place="keep", who=["mara"])
    (before,) = _run(events=[born], scenes=[early])
    assert before.code == "before_birth" and "(year 4)" in before.message
    covers = {item.check: item.covers for item in COVERAGE}
    assert "“year 5” means some time in year 5" in covers["simultaneity"]


def test_a_length_is_not_checked_against_the_window_an_event_happens_in() -> None:
    # "Between year 10 and 12" says when it happens, not that it lasts only two years.
    siege = _event("siege", Between(earliest=10, latest=12))
    lasts = HowLong(lasting=[Lasting(subject_kind="event", subject_id="siege", amount=5)])
    assert not _run(events=[siege], how_long=lasts)


def test_warnings_and_notes_are_capped_apart_and_the_rest_counted(monkeypatch) -> None:
    import backend.domain.continuity as continuity

    monkeypatch.setattr(continuity, "MAX_NOTES", 2)
    monkeypatch.setattr(continuity, "MAX_WARNINGS", 5)
    owners = [_entity(f"c{i}", "Character") for i in range(4)]
    shared = [_fact(f"own{i}", f"c{i}", "OWNS", "sword") for i in range(4)]  # six notes
    dies = _event("mara_dies", At(at=10), kind="death", who=["mara"])
    after = _scene("after", At(at=12), place="keep", who=["mara"])
    found = _run(facts=shared, events=[dies], scenes=[after], more=owners)
    # The warning found after the notes is still there.
    assert _codes(found) == ["after_death", "two_holders", "two_holders"]
    assert found.not_shown == 4


def test_the_table_reads_back_every_row_it_can() -> None:
    raw = {
        "travel": [
            {"from_id": "harbor", "to_id": "keep", "amount": 2},
            {"from_id": "harbor", "to_id": "harbor", "amount": 1},
            {"from_id": "keep", "to_id": "harbor", "amount": 9},
        ],
        "lasting": [{"subject_kind": "event", "subject_id": "siege", "amount": -1}],
    }
    table = read_how_long(raw)
    assert [(row.from_id, row.to_id, row.amount) for row in table.travel] == [("harbor", "keep", 2)]
    assert table.lasting == []


def test_problems_with_a_table_are_said_plainly_and_name_what_is_wrong() -> None:
    places = {item.id: item for item in PEOPLE}
    table = HowLong(
        travel=[TravelTime(from_id="harbor", to_id="mara", amount=1)],
        lasting=[
            Lasting(subject_kind="event", subject_id="gone", amount=1),
            Lasting(subject_kind="fact", subject_id="gone", amount=1),
        ],
    )
    problems = known_records(table, places, [], [])
    assert problems == [
        "The journey between Lantern Harbor and Mara names Mara, which is not a place.",
        "A length names an event that is not in the story.",
        "A length names a fact that is not in the story bible.",
    ]
    missing = HowLong(travel=[TravelTime(from_id="harbor", to_id="nowhere", amount=1)])
    assert known_records(missing, places, [], []) == [
        "A journey from Lantern Harbor names a place that is not in the story bible."
    ]


def test_many_scenes_and_facts_are_checked_quickly() -> None:
    import time

    # Loose scene and event times, and relationships among people never in a scene: each
    # change looks for a scene or an event then, which used to read every one of them.
    people = [_entity(f"p{i}", "Character") for i in range(150)]
    others = [_entity(f"q{i}", "Character") for i in range(150)]
    places = [_entity(f"l{i}", "Location") for i in range(40)]
    scenes = [
        _scene(
            f"s{i}",
            Between(earliest=i, latest=i + 3000),
            place=f"l{i % 40}",
            who=[f"p{(i * 7 + k) % 150}" for k in range(6)],
        )
        for i in range(2000)
    ]
    events = [
        _event(
            f"e{i}", Between(earliest=i, latest=i + 3000), who=[f"p{i % 150}"], where=[f"l{i % 40}"]
        )
        for i in range(2000)
    ]
    facts = [
        _fact(
            f"k{i}",
            f"q{i % 150}",
            "KNOWS",
            f"q{(i + 1) % 150}",
            starts=At(at=i + 0.5),
            ends=At(at=i + 1000.5),
        )
        for i in range(2000)
    ]
    travel = HowLong(
        travel=[TravelTime(from_id=f"l{i}", to_id=f"l{i + 1}", amount=0.5) for i in range(39)]
    )
    began = time.perf_counter()
    found = _run(
        scenes=scenes,
        events=events,
        facts=facts,
        how_long=travel,
        more=[*people, *others, *places],
        unit="hour",
    )
    # Generous: about seven seconds before, a fraction of one now.
    assert time.perf_counter() - began < 3
    assert found and found.not_shown > 0
