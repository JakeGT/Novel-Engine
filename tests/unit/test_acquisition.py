"""How a belief was learned and when (KNW-002); what is learned after a scene (CON-006)."""

import pytest

from backend.domain.acquisition import (
    Placement,
    held_at,
    how_learned,
    learned_after,
    learned_later,
    said_before_learned,
)
from backend.domain.canon.assertions import Belief, Fact, FactVisibility
from backend.domain.canon.events import Event, EventClaim, EventParticipant
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.worldtime import Interval

pytestmark = pytest.mark.unit

SCOPE = {"workspace_id": "w", "project_id": "p", "branch_id": "b"}


def _prop(subject: str = "mara", target: str = "guild") -> Proposition:
    return Proposition(
        subject_id=subject,
        predicate="MEMBER_OF",
        object=EntityRefObject(entity_id=target, project_id="p"),
    )


def _belief(id_: str, source: str | None, holder: str = "tobin", **extra) -> Belief:
    return Belief(
        id=id_,
        holder_entity_id=holder,
        proposition=extra.pop("proposition", _prop()),
        acquisition_provenance_id="prov",
        source_ref=source,
        created_revision=extra.pop("rev", 1),
        **SCOPE,
        **extra,
    )


def _event(id_: str, kind: str, **extra) -> Event:
    return Event(
        id=id_,
        kind=kind,
        name=extra.pop("name", id_),
        provenance_id="prov",
        created_revision=1,
        **SCOPE,
        **extra,
    )


def _where(**moments) -> Placement:
    return Placement(
        scene_index={"s1": 0, "s2": 1, "s3": 2},
        scene_moment={"s1": moments.get("s1"), "s2": moments.get("s2"), "s3": moments.get("s3")},
        event_moment={key: value for key, value in moments.items() if key.startswith("e")},
        scene_titles={"s1": "One", "s2": "Two", "s3": "Three"},
    )


def test_how_follows_the_event_and_the_holders_part_in_it():
    told = _event(
        "e1",
        "telling",
        participants=[
            EventParticipant(entity_id="mara", role="speaker"),
            EventParticipant(entity_id="tobin", role="listener"),
            EventParticipant(entity_id="ines", role="overheard"),
        ],
        scene_id="s2",
    )
    inferred = _event("e2", "inferred", premises=["fact:a", "belief:b"])
    claim = _event(
        "e3",
        "claim",
        claim=EventClaim(proposition=_prop(), scene_id="s3"),
        participants=[EventParticipant(entity_id="mara", role="speaker")],
    )
    events = {e.id: e for e in (told, inferred, claim, _event("e4", "storm"))}
    assert how_learned(_belief("b1", "e1"), events).how == "told"
    assert how_learned(_belief("b1", "e1"), events).source_id == "mara"
    assert how_learned(_belief("b1", "e1"), events).scene_id == "s2"
    assert how_learned(_belief("b2", "e1", holder="ines"), events).how == "overheard"
    worked = how_learned(_belief("b3", "e2"), events)
    assert worked.how == "worked_out" and worked.premises == ("fact:a", "belief:b")
    heard = how_learned(_belief("b4", "e3"), events)
    assert heard.how == "heard_said" and heard.scene_id == "s3"
    assert how_learned(_belief("b5", "e4"), events).how == "event"
    assert how_learned(_belief("b6", "author"), events).how == "recorded"


def test_after_a_scene_by_story_time_first_then_reading_order():
    events = {"e1": _event("e1", "telling", scene_id="s3")}
    learned = how_learned(_belief("b", "e1"), events)
    # Only reading order: scene three is after scene one, not after scene three.
    assert learned_after(learned, "s1", _where()) is True
    assert learned_after(learned, "s3", _where()) is False
    # Story time says it came first (a flashback scene read later): not after.
    timed = _where(s1=Interval(10, 10), e1=Interval(2, 2))
    assert learned_after(learned, "s1", timed) is False
    # Times that overlap say nothing: reading order decides.
    loose = _where(s1=Interval(0, 20), e1=Interval(5, 5))
    assert learned_after(learned, "s1", loose) is True
    # An event with no scene and a later time.
    events = {"e9": _event("e9", "witnessed")}
    seen = how_learned(_belief("b", "e9"), events)
    assert learned_after(seen, "s1", _where(s1=Interval(1, 1), e9=Interval(4, 4))) is True
    # Nothing placed: never a guess.
    assert learned_after(seen, "s1", _where()) is False
    assert learned_after(how_learned(_belief("b", "author"), {}), "s1", _where()) is False


def test_learned_twice_counts_from_the_first_and_common_knowledge_is_known_all_along():
    events = {
        "late": _event("late", "telling", scene_id="s3"),
        "early": _event("early", "telling", scene_id="s1"),
    }
    late = _belief("b1", "late")
    found = learned_later("tobin", "s2", [late], [], events, _where())
    assert [item.belief.id for item in found] == ["b1"]
    assert found[0].learned.scene_id == "s3"
    both = [late, _belief("b2", "early", rev=2)]
    assert learned_later("tobin", "s2", both, [], events, _where()) == []
    common = Fact(
        id="f",
        proposition=_prop(),
        provenance_id="p",
        created_revision=1,
        visibility=FactVisibility.COMMON,
        **SCOPE,
    )
    assert learned_later("tobin", "s2", [late], [common], events, _where()) == []
    # Held at the scene: everything but what is learned later, for that holder only.
    other = _belief("b3", "late", holder="ines")
    assert [b.id for b in held_at("tobin", "s2", [late, other], events, _where())] == ["b3"]


def test_a_claim_said_before_its_speaker_learns_it():
    events = {
        "told": _event("told", "telling", scene_id="s3"),
        "said": _event(
            "said",
            "claim",
            claim=EventClaim(proposition=_prop(), scene_id="s1"),
            participants=[EventParticipant(entity_id="tobin", role="speaker")],
        ),
        "asked": _event(
            "asked",
            "claim",
            claim=EventClaim(proposition=_prop(), scene_id="s1", modality="wonders"),
            participants=[EventParticipant(entity_id="tobin", role="speaker")],
        ),
    }
    found = said_before_learned(list(events.values()), [_belief("b", "told")], [], events, _where())
    assert [(item.event.id, item.speaker_id, item.scene_id) for item in found] == [
        ("said", "tobin", "s1")
    ]
