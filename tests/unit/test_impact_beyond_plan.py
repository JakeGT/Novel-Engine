"""What a change touches beyond the plan (CON-017): beliefs and knowledge, events,
pictures and the studio's checks. Pure and exact."""

import pytest

from backend.domain.canon.assertions import Belief
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.impact import (
    PictureLink,
    StoryFacts,
    StudioFinding,
    Touched,
    belief_dependents,
    event_dependents,
    finding_dependents,
    picture_dependents,
    standing_gaps,
)
from backend.domain.rules import WorldFact

pytestmark = pytest.mark.unit

NAMES = {"m": "Mara", "t": "Tobin", "g": "Harbor Guild", "h": "Lantern Harbor"}


def _belief(id: str, holder: str, subject: str, predicate: str, other: str) -> Belief:
    return Belief(
        id=id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        holder_entity_id=holder,
        proposition=Proposition(
            subject_id=subject,
            predicate=predicate,
            object=EntityRefObject(entity_id=other, project_id="p"),
        ),
        acquisition_provenance_id="prv",
        created_revision=1,
    )


def _event(id: str, name: str, people: list[str], places: list[str] = ()) -> Event:
    return Event(
        id=id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        kind="meeting",
        name=name,
        participants=[EventParticipant(entity_id=p, role="participant") for p in people],
        location_ids=list(places),
        provenance_id="prv",
        created_revision=1,
    )


def _story(**fields) -> StoryFacts:
    return StoryFacts(names=NAMES, facts=fields.pop("facts", {}), **fields)


def test_a_character_who_knows_a_changed_fact_is_affected():
    fact = WorldFact(id="f1", subject_id="m", predicate="MEMBER_OF", object_id="g")
    story = _story(
        facts={"f1": fact},
        belief_records={
            "b1": _belief("b1", "t", "m", "MEMBER_OF", "g"),
            "b2": _belief("b2", "t", "m", "LOCATED_AT", "h"),
            "b3": _belief("b3", "g", "t", "KNOWS", "t"),
        },
    )
    touched = Touched(facts={"f1": "Mara is a member of Harbor Guild"})
    touched.entry("m", "Mara")
    (tobin,) = belief_dependents(touched, story)
    assert tobin.kind == "belief" and tobin.title == "Tobin" and tobin.id == "t"
    assert tobin.reasons[0] == "Knows what changes: Mara is a member of Harbor Guild"
    assert any(reason.startswith("Believes something about Mara") for reason in tobin.reasons)


def test_a_closed_belief_is_not_counted():
    story = _story(
        belief_records={
            "b1": _belief("b1", "t", "m", "LOCATED_AT", "h").model_copy(
                update={"closed_revision": 4}
            )
        }
    )
    touched = Touched()
    touched.entry("m", "Mara")
    assert belief_dependents(touched, story) == []


def test_events_with_a_changed_entry_or_changed_by_the_change_are_affected():
    story = _story(
        event_records={
            "e1": _event("e1", "The guild meeting", ["m"]),
            "e2": _event("e2", "The storm", [], ["h"]),
            "e3": _event("e3", "Tobin sails", ["t"]),
        }
    )
    touched = Touched(events={"e3": "The change removes it"})
    touched.entry("m", "Mara")
    touched.entry("h", "Lantern Harbor")
    found = {item.title: item.reasons for item in event_dependents(touched, story, touched.events)}
    assert found == {
        "The guild meeting": ["Mara takes part in it"],
        "The storm": ["It happens at Lantern Harbor"],
        "Tobin sails": ["The change removes it"],
    }


def test_pictures_of_a_changed_entry_are_affected_once_each():
    touched = Touched()
    touched.entry("m", "Mara")
    found = picture_dependents(
        touched,
        [
            PictureLink("ast_1", "Mara at the wheel", "m", "DEPICTS"),
            PictureLink("ast_1", "Mara at the wheel", "m", "ATTACHED_TO"),
            PictureLink("ast_2", "Tobin", "t", "DEPICTS"),
        ],
    )
    (picture,) = found
    assert picture.id == "ast_1" and picture.reasons[0] == "A picture linked to Mara"
    assert "A picture of Mara" in picture.reasons


def test_studio_checks_naming_a_changed_entry_by_any_name_are_affected_and_others_not():
    touched = Touched()
    touched.entry("m", "Mara")
    found = finding_dependents(
        touched,
        [
            StudioFinding("s1", "The Docks", "Mara is in two places at once.", "check"),
            StudioFinding("s1", "The Docks", "The Fox cannot know this yet.", "note"),
            StudioFinding("s2", "The Market", "A maraschino cherry is stock phrasing.", "check"),
        ],
        {"m": ("Mara", "The Fox")},
    )
    (docks,) = found
    assert docks.kind == "finding" and docks.plan_id == "s1" and docks.count == 2
    assert docks.reasons == [
        "A check says: “Mara is in two places at once.”",
        "A note says: “The Fox cannot know this yet.”",
    ]


def test_the_gaps_still_say_what_is_not_looked_at():
    touched = Touched()
    touched.entry("m", "Mara")
    gaps = " ".join(standing_gaps(touched))
    assert "What the checks in the scene studio would find after the change" in gaps
    assert "Who learns it from whom" in gaps
