"""Plan order checks, promises to the reader and scene conditions in time (STY-005, STY-007)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.domain.canon.assertions import Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.canon.propositions import Proposition
from backend.domain.knowledge import Reveal, reading_order, scene_reveals, with_reveals
from backend.domain.plan_checks import order_findings, promises, reveal_findings
from backend.domain.rules import Check, Pattern
from backend.domain.scene_conditions import (
    CONDITIONS_KEY,
    SceneCondition,
    scene_conditions,
    with_conditions,
)
from backend.domain.threads import with_advances
from backend.services.plan_service import condition_states
from backend.services.story_time_service import Story

pytestmark = pytest.mark.unit


def _plan(id_: str, kind: PlanKind = PlanKind.SCENE, position: int = 0, **fields) -> PlanItem:
    return PlanItem(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        kind=kind,
        title=id_.title(),
        position=position,
        provenance_id="prov",
        created_revision=1,
        **fields,
    )


def _fact(id_: str, subject: str, predicate: str, target: str, **fields) -> Fact:
    return Fact(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        proposition=Proposition(
            subject_id=subject,
            predicate=predicate,
            object={"kind": "entity_ref", "entity_id": target, "project_id": "p"},
        ),
        provenance_id="prov",
        created_revision=1,
        **fields,
    )


def _entity(id_: str, name: str, type_: str = "Character") -> CanonEntity:
    return CanonEntity(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        type=type_,
        name=name,
        schema_version_id="s",
        created_revision=1,
    )


def _reveals(*reveals: Reveal) -> dict[str, str]:
    return with_reveals({}, list(reveals))


NAMES = {"mara": "Mara", "guild": "the Harbor Guild"}
IN_GUILD = _fact("f1", "mara", "MEMBER_OF", "guild")


# ------------------------------------------------------------------ reveals


def test_showing_a_fact_before_the_scene_where_it_comes_out_is_too_early() -> None:
    scenes = [
        _plan("docks", position=0, details=_reveals(Reveal(fact_id="f1", how="shown"))),
        _plan("market", position=1, details=_reveals(Reveal(fact_id="f1", how="hinted"))),
        _plan("reckoning", position=2, details=_reveals(Reveal(fact_id="f1", main_reveal=True))),
    ]
    found = reveal_findings(scenes, [IN_GUILD], NAMES)
    assert [(f.plan_id, f.code, f.kind) for f in found] == [
        ("docks", "reveals_too_early", "problem")
    ]
    assert "Mara is a member of the Harbor Guild" in found[0].message
    assert "“Reckoning”" in found[0].message


def test_a_reveal_with_no_clue_before_it_is_noted_and_one_marked_twice_is_a_problem() -> None:
    scenes = [
        _plan("one", position=0, details=_reveals(Reveal(fact_id="f1", main_reveal=True))),
        _plan("two", position=1, details=_reveals(Reveal(fact_id="f1", main_reveal=True))),
    ]
    found = {(f.plan_id, f.code, f.kind) for f in reveal_findings(scenes, [IN_GUILD], NAMES)}
    assert found == {
        ("two", "planned_reveal_twice", "problem"),
        ("one", "reveal_without_clue", "gap"),
    }
    # A fact no longer in the story is not checked at all.
    closed = IN_GUILD.model_copy(update={"closed_revision": 4})
    assert reveal_findings(scenes, [closed], NAMES) == []
    # Without a scene marked as where it comes out, plain showings are not judged.
    plain = [_plan("one", details=_reveals(Reveal(fact_id="f1")))]
    assert reveal_findings(plain, [IN_GUILD], NAMES) == []


def test_where_a_fact_comes_out_is_shown_plainly_and_stored_only_when_set() -> None:
    with pytest.raises(ValidationError):
        Reveal(fact_id="f1", how="hinted", main_reveal=True)
    stored = _reveals(Reveal(fact_id="f1"), Reveal(fact_id="f2", main_reveal=True))
    assert json.loads(stored["_reveals"]) == [
        {"fact_id": "f1", "how": "shown"},
        {"fact_id": "f2", "how": "shown", "main_reveal": True},
    ]
    assert [r.main_reveal for r in scene_reveals(stored)] == [False, True]


# -------------------------------------------------------------------- order


def test_a_thread_resolved_or_moved_along_before_it_opens_is_out_of_order() -> None:
    items = [
        _plan("t", PlanKind.THREAD),
        _plan("first", position=0, pays_off=["t"]),
        _plan("second", position=1, details=with_advances({}, ["t"])),
        _plan("third", position=2, sets_up=["t"]),
    ]
    found = order_findings(items)
    assert [(f.plan_id, f.code) for f in found] == [
        ("first", "resolves_before_opening"),
        ("second", "moves_along_before_opening"),
    ]
    assert "“Third” opens it" in found[0].message
    # In order, nothing is said.
    fine = [
        _plan("t", PlanKind.THREAD),
        _plan("first", position=0, sets_up=["t"]),
        _plan("second", position=1, pays_off=["t"]),
    ]
    assert order_findings(fine) == []


# ----------------------------------------------------------------- promises


def test_promises_say_where_each_is_made_and_kept_and_how_long_the_reader_waits() -> None:
    items = [
        _plan("kept", PlanKind.THREAD, 0),
        _plan("owed", PlanKind.THREAD, 1),
        _plan("never", PlanKind.THREAD, 2),
        _plan(
            "s1",
            position=0,
            sets_up=["kept"],
            details=_reveals(Reveal(fact_id="f1", how="hinted")),
        ),
        _plan("s2", position=1, sets_up=["owed"], details=with_advances({}, ["kept"])),
        _plan("s3", position=2, details=_reveals(Reveal(fact_id="f2", how="shown"))),
        _plan(
            "s4",
            position=3,
            pays_off=["kept"],
            details=_reveals(Reveal(fact_id="f1", how="shown"), Reveal(fact_id="f2", how="hinted")),
        ),
        _plan("s5", position=4, details=_reveals(Reveal(fact_id="f3", how="hinted"))),
    ]
    facts = [IN_GUILD, _fact("f2", "mara", "KNOWS", "guild"), _fact("f3", "mara", "OWNS", "guild")]
    found = promises(items, reading_order(items), facts)
    summary = [
        (p.kind, p.subject_id, p.made_at, p.kept_at, p.distance, p.moved_along) for p in found
    ]
    assert summary == [
        ("thread", "kept", 1, 4, 3, ["s2"]),
        ("clue", "f1", 1, 4, 3, []),
        ("thread", "owed", 2, None, 3, []),
        # f2 was shown plainly before its hint, so the hint promises nothing.
        ("clue", "f3", 5, None, 0, []),
    ]
    assert [p.kept for p in found] == [True, True, False, False]


def test_a_thread_resolved_before_it_opens_is_a_promise_kept_out_of_order() -> None:
    items = [
        _plan("t", PlanKind.THREAD),
        _plan("s1", position=0, pays_off=["t"]),
        _plan("s2", position=1, sets_up=["t"]),
    ]
    (promise,) = promises(items, reading_order(items), [])
    assert promise.out_of_order and not promise.kept
    assert (promise.made_in, promise.kept_in, promise.distance) == ("s2", "s1", 1)


# --------------------------------------------------------------- conditions


def _located(target: str) -> Check:
    return Check(kind="has", pattern=Pattern(predicate="LOCATED_AT", other_id=target))


def _story(*facts: Fact) -> Story:
    entities = [_entity("mara", "Mara"), _entity("harbor", "Lantern Harbor", "Location")]
    return Story(entities={e.id: e for e in entities}, facts=list(facts), events=[])


def _scene(id_: str, position: int, conditions: list[SceneCondition], at: float | None = None):
    details = with_conditions({}, conditions)
    if at is not None:
        details["_when"] = json.dumps({"kind": "at", "at": at})
    return _plan(id_, position=position, details=details)


def test_a_condition_is_judged_at_the_scenes_own_point_in_story_time() -> None:
    need = SceneCondition(when="before", entity_id="mara", check=_located("harbor"))
    arrives = _fact("f1", "mara", "LOCATED_AT", "harbor", starts={"kind": "at", "at": 10})
    scenes = [_scene("early", 0, [need], at=5), _scene("late", 1, [need], at=12)]
    early, late = condition_states(scenes, _story(arrives))
    assert (early.status, early.holds) == ("not_met", False)
    assert "when this scene happens" in early.note
    assert (late.status, late.holds) == ("met", True)
    # Untimed, the story as it stands is used: she is there.
    (untimed,) = condition_states([_scene("any", 0, [need])], _story(arrives))
    assert untimed.status == "met"
    # A fuzzy start that may or may not be before the scene cannot be told.
    fuzzy = _fact(
        "f1", "mara", "LOCATED_AT", "harbor", starts={"kind": "between", "earliest": 4, "latest": 8}
    )
    (unclear,) = condition_states([_scene("mid", 0, [need], at=6)], _story(fuzzy))
    assert (unclear.status, unclear.holds) == ("unclear", None)


def test_earlier_scenes_after_conditions_plan_or_undo_what_a_later_scene_needs() -> None:
    there = SceneCondition(when="before", entity_id="mara", check=_located("harbor"))
    brings = SceneCondition(when="after", entity_id="mara", check=_located("harbor"))
    leaves = SceneCondition(
        when="after",
        entity_id="mara",
        check=Check(kind="lacks", pattern=Pattern(predicate="LOCATED_AT", other_id="harbor")),
    )
    planned = condition_states(
        [_scene("arrive", 0, [brings]), _scene("meet", 1, [there])], _story()
    )
    assert [(s.plan_id, s.when, s.status) for s in planned] == [
        ("arrive", "after", "not_met"),
        ("meet", "before", "planned"),
    ]
    assert planned[1].planned_in == "arrive" and "“Arrive”" in planned[1].note
    # The nearest earlier scene wins: leaving after arriving means she is not there.
    undone = condition_states(
        [_scene("arrive", 0, [brings]), _scene("leave", 1, [leaves]), _scene("meet", 2, [there])],
        _story(_fact("f1", "mara", "LOCATED_AT", "harbor")),
    )
    assert undone[-1].status == "not_met" and undone[-1].holds is True
    assert "“Leave”" in undone[-1].note


def test_conditions_can_be_preferred_or_in_words_and_store_as_before() -> None:
    soft = SceneCondition(
        when="before", strength="soft", entity_id="mara", check=_located("harbor")
    )
    words = SceneCondition(when="before", words="  It is raining.  ")
    assert words.words == "It is raining."
    states = condition_states([_scene("s", 0, [soft, words])], _story())
    assert [(s.strength, s.status, s.index) for s in states] == [
        ("soft", "not_met", 0),
        ("hard", "unchecked", 1),
    ]
    assert states[1].reading == "It is raining."
    # A required condition is stored exactly as before strengths existed.
    hard = SceneCondition(when="before", entity_id="mara", check=_located("harbor"))
    stored = json.loads(with_conditions({}, [hard, soft])[CONDITIONS_KEY])
    assert "strength" not in stored[0] and stored[1]["strength"] == "soft"
    assert scene_conditions(with_conditions({}, [hard, soft, words])) == [hard, soft, words]
    with pytest.raises(ValidationError):
        SceneCondition(when="before", words="Raining", entity_id="mara", check=_located("x"))
    with pytest.raises(ValidationError):
        SceneCondition(when="before", entity_id="mara")
    with pytest.raises(ValidationError):
        SceneCondition(when="before", words="   ")


def test_an_untimed_scene_cannot_tell_a_condition_that_turns_on_a_fact_that_ends() -> None:
    gone = SceneCondition(
        when="before",
        entity_id="mara",
        check=Check(kind="lacks", pattern=Pattern(predicate="LOCATED_AT", other_id="harbor")),
    )
    one_place = SceneCondition(
        when="before",
        entity_id="mara",
        check=Check(kind="at_most", count=1, pattern=Pattern(predicate="LOCATED_AT")),
    )
    # She was at the harbor until year 10, then at the castle: both facts are current.
    left = _fact("f1", "mara", "LOCATED_AT", "harbor", ends={"kind": "at", "at": 10})
    castle = _fact("f2", "mara", "LOCATED_AT", "castle", starts={"kind": "at", "at": 10})
    story = _story(left, castle)
    story.entities["castle"] = _entity("castle", "Grey Castle", "Location")

    states = condition_states([_scene("any", 0, [gone, one_place])], story)
    assert [(s.status, s.holds) for s in states] == [("unclear", None), ("unclear", None)]
    assert "no time in the story" in states[0].note

    # A fact with no end still decides it: she is at the harbor, so it is not met.
    (still,) = condition_states(
        [_scene("any", 0, [gone])], _story(_fact("f1", "mara", "LOCATED_AT", "harbor"))
    )
    assert (still.status, still.holds) == ("not_met", False)
    # With its own time, the scene is judged at that moment as before.
    (timed,) = condition_states([_scene("late", 0, [gone], at=12)], story)
    assert timed.status == "met"
