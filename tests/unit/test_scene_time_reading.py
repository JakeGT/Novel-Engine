"""Facts read from a timed scene start at its time (EXT-007): the deterministic parts."""

from __future__ import annotations

from backend.domain.canon.assertions import Fact
from backend.domain.canon.propositions import Proposition
from backend.domain.canon.registry import CORE_PREDICATES
from backend.domain.operations import (
    AmendFactOp,
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    EvidenceRef,
)
from backend.domain.worldtime import At, Between
from backend.services.canon_creation_workflow import _already_so, _until_the_scene
from backend.services.intake_workflow import _from_scene_time

SCENE = At(at=10)


def _prop(predicate: str, target: str = "ent_b") -> Proposition:
    return Proposition(
        subject_id="ent_a",
        predicate=predicate,
        object={"kind": "entity_ref", "entity_id": target, "project_id": "p"},
    )


def _assert(predicate: str, key: str = "f0", **fields) -> AssertFactOp:
    return AssertFactOp(
        id="tmp:f",
        proposition=_prop(predicate),
        evidence=[EvidenceRef(source_artefact_id="v", location=f"question:{key}")],
        **fields,
    )


def _fact(fact_id: str, **fields) -> Fact:
    return Fact(
        id=fact_id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        proposition=_prop("LOCATED_AT", "ent_old"),
        provenance_id="prov",
        created_revision=1,
        **fields,
    )


def test_only_kinds_of_fact_that_take_a_time_start_at_the_scene():
    timed = _from_scene_time(_assert("LOCATED_AT"), SCENE, CORE_PREDICATES, {"f0"})
    assert isinstance(timed, AssertFactOp) and timed.starts == SCENE
    member = _from_scene_time(_assert("MEMBER_OF"), SCENE, CORE_PREDICATES, {"f0"})
    assert isinstance(member, AssertFactOp) and member.starts is None


def test_only_what_the_scene_shows_starting_takes_its_time():
    # "The castle stands on the hill": not shown changing here, so it keeps no start.
    standing = _from_scene_time(_assert("LOCATED_AT", key="f3"), SCENE, CORE_PREDICATES, {"f0"})
    assert isinstance(standing, AssertFactOp) and standing.starts is None
    arrives = _from_scene_time(_assert("LOCATED_AT", key="f3"), SCENE, CORE_PREDICATES, {"f3"})
    assert isinstance(arrives, AssertFactOp) and arrives.starts == SCENE


def test_already_timed_facts_keep_their_time():
    own = _from_scene_time(_assert("LOCATED_AT", starts=At(at=2)), SCENE, CORE_PREDICATES, {"f0"})
    assert isinstance(own, AssertFactOp) and own.starts == At(at=2)
    entity = CreateEntityOp(id="tmp:e", type="Character", name="Ana")
    assert _from_scene_time(entity, SCENE, CORE_PREDICATES, {"f0"}) is entity


def test_what_happens_to_the_old_place_when_a_new_one_starts_at_the_scene():
    new = _assert("LOCATED_AT", starts=SCENE)
    untimed = _fact("fct_untimed")
    before = _fact("fct_before", starts=At(at=3))
    over = _fact("fct_over", starts=At(at=1), ends=At(at=4))
    later = _fact("fct_later", starts=At(at=20))
    changes, added = _until_the_scene([untimed, before, over, later], new, SCENE, {})
    # Ended at the scene; left alone when already over or when it starts after it.
    assert [(type(c).__name__, c.id) for c in changes] == [
        ("AmendFactOp", "fct_untimed"),
        ("AmendFactOp", "fct_before"),
    ]
    assert isinstance(changes[0], AmendFactOp) and changes[0].ends == SCENE
    # The new place lasts until the later one starts.
    assert added.starts == SCENE and added.ends == At(at=20)


def test_a_new_place_ends_at_the_first_of_several_later_ones():
    new = _assert("LOCATED_AT", starts=SCENE)
    facts = [_fact("fct_30", starts=At(at=30)), _fact("fct_20", starts=At(at=20))]
    changes, added = _until_the_scene(facts, new, SCENE, {})
    assert changes == [] and added.ends == At(at=20)
    alone, untouched = _until_the_scene([_fact("fct_untimed")], new, SCENE, {})
    assert untouched.ends is None and len(alone) == 1


def test_a_scene_whose_time_is_not_one_moment_removes_the_old_place_as_before():
    fuzzy = Between(earliest=8, latest=12)
    changes, _ = _until_the_scene(
        [_fact("fct_untimed")], _assert("LOCATED_AT", starts=fuzzy), fuzzy, {}
    )
    assert [type(c) for c in changes] == [CloseFactOp]


def test_going_back_to_an_earlier_place_is_not_taken_for_a_repeat():
    # London until scene 5, Paris from it; scene 9 goes back to London.
    london = _fact("fct_london", ends=At(at=5))
    paris = _fact("fct_paris", starts=At(at=5)).model_copy(
        update={"proposition": _prop("LOCATED_AT", "ent_paris")}
    )
    back = AssertFactOp(
        id="tmp:f",
        proposition=_prop("LOCATED_AT", "ent_old"),
        evidence=[EvidenceRef(source_artefact_id="v", location="question:f0")],
        starts=At(at=9),
    )
    assert not _already_so([london, paris], back, At(at=9), {})
    changes, _ = _until_the_scene([london, paris], back, At(at=9), {})
    # Paris ends at scene 9; the London that ended at 5 is left as it was.
    assert [(type(c).__name__, c.id) for c in changes] == [("AmendFactOp", "fct_paris")]
    # Still in London at the scene: nothing new to add.
    assert _already_so([_fact("fct_london", starts=At(at=1))], back, At(at=9), {})
    assert _already_so([_fact("fct_london")], back, At(at=9), {})
