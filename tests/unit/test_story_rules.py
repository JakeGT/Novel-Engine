"""One evaluator answers for rules, their examples, scene conditions and proposed changes."""

import pytest

from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.operations import (
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    RetireEntityOp,
    UpdateEntityOp,
)
from backend.domain.rules import (
    Check,
    ExampleEntry,
    ExampleFact,
    Pattern,
    RuleBody,
    RuleExample,
    Selector,
    World,
    WorldEntry,
    WorldFact,
    apply_operations,
    read_rule,
    run_examples,
    violations,
    world_from_example,
)
from backend.domain.scene_conditions import (
    SceneCondition,
    condition_state,
    scene_conditions,
    with_conditions,
)

pytestmark = pytest.mark.unit

OWNS_ONE = Check(kind="at_most", count=1, pattern=Pattern(predicate="OWNS", direction="in"))
ONE_OWNER = RuleBody(applies_to=Selector(type="Object"), checks=[OWNS_ONE])


def _world() -> World:
    return World(
        entries={
            "ring": WorldEntry("ring", "Object", "The Ring", frozenset({"cursed"})),
            "mara": WorldEntry("mara", "Character", "Mara"),
            "ivo": WorldEntry("ivo", "Character", "Ivo"),
        },
        facts={"f1": WorldFact("f1", "mara", "OWNS", object_id="ring")},
    )


def _owns(fact_id: str, owner: str) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(
            subject_id=owner,
            predicate="OWNS",
            object=EntityRefObject(entity_id="ring", project_id="p"),
        ),
    )


def test_a_rule_its_example_a_scene_condition_and_a_proposed_change_agree() -> None:
    world = _world()
    after = apply_operations(world, [_owns("f2", "ivo")])
    condition = SceneCondition(when="after", entity_id="ring", check=OWNS_ONE)
    example = RuleExample(
        description="Two owners",
        entries=[
            ExampleEntry(ref="ring", type="Object", name="The Ring"),
            ExampleEntry(ref="mara", type="Character", name="Mara"),
            ExampleEntry(ref="ivo", type="Character", name="Ivo"),
        ],
        facts=[
            ExampleFact(subject_ref="mara", predicate="OWNS", object_ref="ring"),
            ExampleFact(subject_ref="ivo", predicate="OWNS", object_ref="ring"),
        ],
        expect="caught",
    )
    # The same story, reached three ways, gets the same answer from each.
    assert violations("r", ONE_OWNER, world) == []
    assert condition_state("scene", condition, world).holds is True
    broken = [item.entity_id for item in violations("r", ONE_OWNER, after)]
    assert broken == ["ring"]
    assert condition_state("scene", condition, after).holds is False
    assert [r.outcome for r in run_examples(ONE_OWNER, [example])] == ["caught"]
    assert [
        item.entity_id for item in violations("r", ONE_OWNER, world_from_example(example))
    ] == broken
    # And the same answer every time.
    assert violations("r", ONE_OWNER, after) == violations("r", ONE_OWNER, after)


def test_changes_are_applied_to_a_copy_in_order() -> None:
    world = _world()
    after = apply_operations(
        world,
        [
            CreateEntityOp(
                id="tmp:crown", type="Object", name="The Crown", attributes={"_tags": ["Royal"]}
            ),
            UpdateEntityOp(id="ring", name="The One Ring"),
            CloseFactOp(id="f1"),
            RetireEntityOp(id="ivo"),
            AssertFactOp(
                id="f3",
                proposition=Proposition(
                    subject_id="ring", predicate="HAS_STATUS", object=StringObject(value="Lost")
                ),
            ),
        ],
    )
    assert world.entries["ring"].name == "The Ring" and "f1" in world.facts
    assert after.entries["ring"].name == "The One Ring" and after.entries["ring"].tags == {"cursed"}
    assert after.entries["tmp:crown"].tags == {"royal"}
    assert "ivo" not in after.entries and "f1" not in after.facts
    lost = RuleBody(
        applies_to=Selector(tag="Cursed"),
        checks=[Check(kind="has", pattern=Pattern(predicate="HAS_STATUS", value="lost"))],
    )
    assert violations("r", lost, after) == []
    assert [v.entity_name for v in violations("r", lost, world)] == ["The Ring"]


@pytest.mark.parametrize(
    ("check", "holds"),
    [
        (Check(kind="has", pattern=Pattern(predicate="OWNS", direction="in")), True),
        (
            Check(kind="has", pattern=Pattern(predicate="OWNS", direction="in", other_id="ivo")),
            False,
        ),
        (
            Check(
                kind="lacks",
                pattern=Pattern(predicate="OWNS", direction="in", other_type="Faction"),
            ),
            True,
        ),
        (Check(kind="at_least", count=2, pattern=Pattern(predicate="OWNS", direction="in")), False),
        (Check(kind="at_most", count=0, pattern=Pattern(predicate="OWNS", direction="in")), False),
        (Check(kind="has", pattern=Pattern(predicate="OWNS", direction="out")), False),
    ],
)
def test_each_kind_of_check(check: Check, holds: bool) -> None:
    body = RuleBody(applies_to=Selector(entity_id="ring"), checks=[check])
    assert (violations("r", body, _world()) == []) is holds


def test_any_and_all_and_who_a_rule_is_about() -> None:
    world = _world()
    never = Check(kind="has", pattern=Pattern(predicate="KNOWS"))
    owned = Check(kind="has", pattern=Pattern(predicate="OWNS", direction="in"))
    about_ring = Selector(entity_id="ring")
    assert violations(
        "r", RuleBody(applies_to=about_ring, mode="all", checks=[never, owned]), world
    )
    assert not violations(
        "r", RuleBody(applies_to=about_ring, mode="any", checks=[never, owned]), world
    )
    owners = Selector(type="Character", having=[Pattern(predicate="OWNS", other_type="Object")])
    found = violations("r", RuleBody(applies_to=owners, checks=[never]), world)
    # Only those who own something are held to it.
    assert [item.entity_name for item in found] == ["Mara"]
    assert read_rule(RuleBody(applies_to=owners, checks=[never]), {}) == (
        "Every character where it owns an object, must have this: it knows something."
    )


def test_a_fact_about_an_entry_that_is_gone_does_not_count() -> None:
    world = _world()
    del world.entries["mara"]
    owned = RuleBody(
        applies_to=Selector(entity_id="ring"),
        checks=[Check(kind="has", pattern=Pattern(predicate="OWNS", direction="in"))],
    )
    assert [item.entity_id for item in violations("r", owned, world)] == ["ring"]


def test_scene_conditions_survive_a_round_trip_and_bad_data_is_no_conditions() -> None:
    condition = SceneCondition(when="before", entity_id="ring", check=OWNS_ONE)
    details = with_conditions({"mood": "cold"}, [condition])
    assert scene_conditions(details) == [condition]
    assert with_conditions(details, []) == {"mood": "cold"}
    assert scene_conditions({"_conditions": "not json"}) == []
    assert scene_conditions({"_conditions": '[{"when": "sometime"}, 3]'}) == []
    gone = condition_state(
        "scene", SceneCondition(when="before", entity_id="nobody", check=OWNS_ONE), _world()
    )
    assert gone.holds is None
