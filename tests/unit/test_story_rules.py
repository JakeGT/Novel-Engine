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


# ------------------------------------------------ the commit's check of every rule


def _rule(body: RuleBody, *, rule_id: str = "rule-1", blocking: bool = True):
    from datetime import UTC, datetime

    from backend.domain.rules import RuleStatus, StoryRule
    from backend.domain.validation import RuleEnforcement

    now = datetime.now(UTC)
    return StoryRule(
        workspace_id="w",
        project_id="p",
        id=rule_id,
        title=f"Rule {rule_id}",
        sentence="",
        status=RuleStatus.ON,
        version=1,
        body=body,
        examples=[],
        created_by=None,
        created_at=now,
        updated_at=now,
        enforcement=RuleEnforcement.BLOCKING if blocking else RuleEnforcement.WARNING,
    )


class _Rules:
    def __init__(self, rules) -> None:
        self.rules = list(rules)
        self.listed = 0

    async def list_rules(self, *, workspace_id: str, project_id: str):
        self.listed += 1
        return self.rules


def _service(world: World, rules):
    from backend.services.rule_service import RuleService

    class _Service(RuleService):
        reads = 0

        async def _world_for(self, context, rules):
            type(self).reads += 1
            return world, True

    return _Service(_Rules(rules), reader=None)  # type: ignore[arg-type]


def _evaluate(service, operations):
    import asyncio

    from backend.domain.validation import ValidationContext

    context = ValidationContext(
        workspace_id="w",
        project_id="p",
        branch_id="b",
        schema_version_id=None,
        head_revision=1,
        reader=object(),  # type: ignore[arg-type]
    )
    return list(asyncio.run(service.evaluate(operations, context=context)))


HAS_MEMBER = RuleBody(
    applies_to=Selector(type="Faction"),
    checks=[Check(kind="has", pattern=Pattern(predicate="MEMBER_OF", direction="in"))],
)


def _member(fact_id: str, who: str, faction: str) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(
            subject_id=who,
            predicate="MEMBER_OF",
            object=EntityRefObject(entity_id=faction, project_id="p"),
        ),
    )


def _empty_factions(count: int) -> World:
    """More factions with nobody in them than a rule ever shows at once."""
    world = World(entries={"ines": WorldEntry("ines", "Character", "Ines")})
    for number in range(count):
        faction = f"n{number:03d}"
        world.entries[faction] = WorldEntry(faction, "Faction", f"N{number:03d}")
    return world


def test_fixing_one_of_many_breaks_is_not_blocked() -> None:
    from backend.domain.rules import MAX_VIOLATIONS

    world = _empty_factions(MAX_VIOLATIONS + 1)
    service = _service(world, [_rule(HAS_MEMBER)])
    # Giving the first empty faction a member fixes it, and breaks nothing.
    assert _evaluate(service, [_member("f-new", "ines", "n000")]) == []


def test_a_break_past_the_shown_handful_still_blocks() -> None:
    from backend.domain.rules import MAX_VIOLATIONS
    from backend.domain.validation import ValidationSeverity, classify_rule_evaluation

    world = _empty_factions(MAX_VIOLATIONS + 1)
    # A faction that holds now, and sorts after every one already broken.
    world.entries["zeal"] = WorldEntry("zeal", "Faction", "Zealots")
    world.facts["f-zeal"] = WorldFact("f-zeal", "ines", "MEMBER_OF", object_id="zeal")
    service = _service(world, [_rule(HAS_MEMBER)])

    (finding,) = _evaluate(service, [CloseFactOp(id="f-zeal")])
    assert "Zealots" in finding.message and finding.operation_ordinal == 0
    assert classify_rule_evaluation(finding) is ValidationSeverity.BLOCKING_RULE


def test_a_change_that_touches_no_entry_or_fact_checks_no_rule() -> None:
    from backend.domain.operations import CreatePlanItemOp

    service = _service(_empty_factions(3), [_rule(HAS_MEMBER)])
    plan_only = [CreatePlanItemOp(id="tmp:scene", kind="scene", title="The harbour")]
    assert _evaluate(service, plan_only) == []
    assert service._store.listed == 0 and type(service).reads == 0  # noqa: SLF001


def test_checking_rules_over_a_large_story_is_quick() -> None:
    """Two thousand entries, ten thousand facts, ten rules: well under a second.

    The bound is generous so a slow machine does not fail it; checking every fact
    for every entry took tens of seconds.
    """
    import time

    world = World()
    for number in range(1000):
        world.entries[f"c{number}"] = WorldEntry(f"c{number}", "Character", f"C{number}")
        world.entries[f"o{number}"] = WorldEntry(f"o{number}", "Object", f"O{number}")
    for number in range(10_000):
        if number % 2:
            world.facts[f"f{number}"] = WorldFact(
                f"f{number}", f"c{number % 1000}", "OWNS", object_id=f"o{(number * 7) % 1000}"
            )
        else:
            world.facts[f"f{number}"] = WorldFact(
                f"f{number}", f"c{number % 1000}", "KNOWS", object_id=f"c{(number * 3) % 1000}"
            )
    bodies = [
        ONE_OWNER,
        RuleBody(
            applies_to=Selector(type="Character"),
            checks=[Check(kind="has", pattern=Pattern(predicate="KNOWS"))],
        ),
        RuleBody(
            applies_to=Selector(type="Character"),
            checks=[
                Check(kind="at_most", count=50, pattern=Pattern(predicate="OWNS")),
                Check(kind="lacks", pattern=Pattern(predicate="OWNS", other_type="Character")),
            ],
        ),
        RuleBody(
            applies_to=Selector(type="Object", having=[Pattern(predicate="OWNS", direction="in")]),
            checks=[
                Check(kind="at_least", count=1, pattern=Pattern(predicate="OWNS", direction="in"))
            ],
        ),
    ]
    rules = [_rule(bodies[number % len(bodies)], rule_id=f"r{number}") for number in range(10)]
    service = _service(world, rules)
    operations = [
        CreateEntityOp(id="c-new", type="Character", name="Newcomer"),
        _owns("f-new", "c-new"),
        CloseFactOp(id="f1"),
    ]

    started = time.perf_counter()
    _evaluate(service, operations)
    for rule in rules:
        violations(rule.id, rule.body, world)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"checking ten rules took {elapsed:.1f}s"


def test_an_entry_brought_back_is_seen_by_the_rules_again() -> None:
    import asyncio

    from backend.domain.canon.entities import CanonEntity, EntityLifecycle
    from backend.domain.operations import RestoreEntityOp
    from backend.domain.rules import affected_entries, world_from_canon
    from backend.domain.validation import ValidationContext

    gone = CanonEntity(
        id="zeal",
        workspace_id="w",
        project_id="p",
        branch_id="b",
        type="Faction",
        name="Zealots",
        schema_version_id="",
        created_revision=1,
        lifecycle=EntityLifecycle.RETIRED,
        retired_revision=2,
    )
    world = world_from_canon([gone], [])
    restore = [RestoreEntityOp(id="zeal")]
    after = apply_operations(world, restore)
    assert "zeal" not in world.entries and after.entries["zeal"].name == "Zealots"
    assert "zeal" in affected_entries(world, after, restore)
    # A retirement keeps the entry aside, so bringing it back in the same set works too.
    assert "ring" in apply_operations(_world(), [RetireEntityOp(id="ring")]).retired

    # Through the commit's rules, a faction brought back with nobody in it breaks one.
    class _Reader:
        async def get_entity(self, entity_id: str):
            return gone if entity_id == "zeal" else None

    service = _service(World(entries={"ines": WorldEntry("ines", "Character", "Ines")}), [])
    service._store = _Rules([_rule(HAS_MEMBER)])  # noqa: SLF001
    context = ValidationContext(
        workspace_id="w",
        project_id="p",
        branch_id="b",
        schema_version_id=None,
        head_revision=2,
        reader=_Reader(),  # type: ignore[arg-type]
    )
    (finding,) = asyncio.run(service.evaluate(restore, context=context))
    assert "Zealots" in finding.message
