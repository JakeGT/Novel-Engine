"""A scene condition about what someone knows, has seen or has heard (STY-005), judged
against the beliefs recorded and how each was learned; and checks other than "has" and
"lacks" in plan order ("is at", "knows", the very same check)."""

import pytest
from pydantic import ValidationError

from backend.domain.acquisition import Placement
from backend.domain.canon.assertions import Belief
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.rules import Check, Pattern, World, WorldEntry
from backend.domain.scene_conditions import (
    Knowing,
    KnowsCheck,
    SceneCondition,
    condition_reading,
    condition_state,
    planned_change,
    statement_key,
)

pytestmark = pytest.mark.unit

SCOPE = {"workspace_id": "w", "project_id": "p", "branch_id": "b"}
NAMES = {"mara": "Mara", "ivo": "Ivo", "tower": "the tower", "dock": "the docks"}
WORLD = World(entries={key: WorldEntry(key, "Character", name) for key, name in NAMES.items()})
AT_TOWER = Proposition(
    subject_id="ivo",
    predicate="LOCATED_AT",
    object=EntityRefObject(entity_id="tower", project_id="p"),
)


def _knows(how: str = "knows", when: str = "before") -> SceneCondition:
    return SceneCondition(
        when=when,
        entity_id="mara",
        knows=KnowsCheck(how=how, subject_id="ivo", predicate="LOCATED_AT", other_id="tower"),
    )


def _event(event_id: str, kind: str, scene: str, role: str = "listener") -> Event:
    return Event(
        id=event_id,
        kind=kind,
        name=event_id,
        provenance_id="prov",
        created_revision=1,
        scene_id=scene,
        participants=[EventParticipant(entity_id="mara", role=role)],
        **SCOPE,
    )


def _belief(source: str | None, holder: str = "mara") -> Belief:
    return Belief(
        id=f"bel_{source}",
        holder_entity_id=holder,
        proposition=AT_TOWER,
        acquisition_provenance_id="prov",
        source_ref=source,
        created_revision=1,
        **SCOPE,
    )


WHERE = Placement(
    scene_index={"s1": 0, "s2": 1, "s3": 2},
    scene_moment={"s1": None, "s2": None, "s3": None},
    event_moment={},
    scene_titles={"s1": "One", "s2": "Two", "s3": "Three"},
)
EVENTS = {
    "seen1": _event("seen1", "witnessed", "s1", role="witness"),
    "told1": _event("told1", "telling", "s1"),
    "told3": _event("told3", "telling", "s3"),
}


def _state(condition: SceneCondition, beliefs: list[Belief], scene: str = "s2", **knowing):
    found = Knowing(beliefs=beliefs, events=EVENTS, where=WHERE, names=NAMES, **knowing)
    return condition_state(scene, condition, WORLD, knowing=found)


def test_a_knowing_condition_is_one_form_and_reads_in_plain_words() -> None:
    assert condition_reading(_knows("seen"), NAMES) == (
        "Mara must have seen this: Ivo is located at the tower."
    )
    assert condition_reading(_knows(), NAMES).startswith("Mara must know this:")
    with pytest.raises(ValidationError):
        SceneCondition(when="before", entity_id="mara")
    with pytest.raises(ValidationError):
        KnowsCheck(subject_id="ivo", predicate="LOCATED_AT")
    with pytest.raises(ValidationError):
        SceneCondition(
            when="before",
            entity_id="mara",
            check=Check(kind="has", pattern=Pattern(predicate="LOCATED_AT")),
            knows=_knows().knows,
        )


def test_knowing_needs_a_belief_learned_before_the_scene() -> None:
    assert _state(_knows(), [_belief("told1")]).status == "met"
    assert _state(_knows(), [_belief(None)]).status == "met"  # recorded: known all along
    later = _state(_knows(), [_belief("told3")])
    assert later.status == "not_met" and "only later, in “Three”" in later.note
    none = _state(_knows(), [_belief("told1", holder="ivo")])
    assert none.status == "not_met" and none.note == "Nothing records that Mara knows this."
    # Everyone knows a common fact.
    common = frozenset({statement_key("ivo", "LOCATED_AT", AT_TOWER.object)})
    assert _state(_knows(), [], common=common).status == "met"


def test_seen_and_heard_need_that_way_of_learning() -> None:
    assert _state(_knows("seen"), [_belief("seen1")]).status == "met"
    told = _state(_knows("seen"), [_belief("told1")])
    assert told.status == "not_met" and "was told it" in told.note and "seeing it" in told.note
    assert _state(_knows("heard"), [_belief("told1")]).status == "met"
    assert _state(_knows("heard"), [_belief("seen1")]).status == "not_met"
    # A common fact is known, but nobody is said to have seen it.
    common = frozenset({statement_key("ivo", "LOCATED_AT", AT_TOWER.object)})
    assert _state(_knows("seen"), [], common=common).status == "not_met"


def test_learned_in_the_scene_itself_is_too_late_before_and_in_time_after() -> None:
    before = _state(_knows(), [_belief("told1")], scene="s1")
    assert before.status == "not_met" and "only in this scene" in before.note
    assert _state(_knows(when="after"), [_belief("told1")], scene="s1").status == "met"


def test_without_the_beliefs_read_a_knowing_condition_is_unclear() -> None:
    assert condition_state("s2", _knows(), WORLD).status == "unclear"


def _at(place: str, when: str = "after", kind: str = "has") -> SceneCondition:
    return SceneCondition(
        when=when,
        entity_id="ivo",
        check=Check(kind=kind, pattern=Pattern(predicate="LOCATED_AT", other_id=place)),
    )


def test_is_at_in_plan_order_is_undone_by_being_left_somewhere_else() -> None:
    need = _at("tower", when="before")
    moved = planned_change(need, [("s1", "One", [_at("dock")])])
    assert moved is not None and not moved.brings_about and moved.title == "One"
    brought = planned_change(need, [("s1", "One", [_at("dock")]), ("s2", "Two", [_at("tower")])])
    assert brought is not None and brought.brings_about and brought.title == "Two"
    # Something else entirely says nothing about it.
    other = SceneCondition(
        when="after",
        entity_id="ivo",
        check=Check(kind="has", pattern=Pattern(predicate="MEMBER_OF", other_id="dock")),
    )
    assert planned_change(need, [("s1", "One", [other])]) is None


def test_knowing_and_other_checks_in_plan_order() -> None:
    assert planned_change(_knows(), [("s1", "One", [_knows(when="after")])]).brings_about
    # Seeing it counts for knowing it, not the other way round.
    assert planned_change(_knows(), [("s1", "One", [_knows("seen", when="after")])]).brings_about
    assert planned_change(_knows("seen"), [("s1", "One", [_knows(when="after")])]) is None
    at_most = Check(kind="at_most", count=1, pattern=Pattern(predicate="OWNS"))
    need = SceneCondition(when="before", entity_id="ivo", check=at_most)
    same = SceneCondition(when="after", entity_id="ivo", check=at_most)
    assert planned_change(need, [("s1", "One", [same])]).brings_about


def test_a_planned_knowing_reads_as_planned_where_its_scene_comes() -> None:
    planned = planned_change(_knows(), [("s1", "One", [_knows(when="after")])])
    found = Knowing(beliefs=[], events=EVENTS, where=WHERE, names=NAMES)
    state = condition_state("s2", _knows(), WORLD, knowing=found, planned=planned)
    assert state.status == "planned" and "“One”" in state.note
