"""What a story rule can say beyond has and lacks (CON-005, CAN-013).

Ranges, implications, incompatibilities, exceptions, beliefs, and rules judged at
every moment of story time or at the end of the story: each one evaluated, read
back in words, versioned, and checked over a large story quickly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.operations import (
    AmendEventOp,
    AmendFactOp,
    AssertBeliefOp,
    AssertFactOp,
    CloseBeliefOp,
    CreateEventOp,
    RetireEntityOp,
)
from backend.domain.rules import (
    RULE_FORM,
    Check,
    ExampleBelief,
    ExampleEntry,
    ExampleFact,
    Exceptions,
    FactIndex,
    Pattern,
    RuleBody,
    RuleExample,
    RuleStatus,
    Selector,
    StoryRule,
    World,
    WorldBelief,
    WorldEntry,
    WorldFact,
    affected_entries,
    apply_operations,
    body_problems,
    moves_time,
    read_rule,
    referenced_ids,
    run_examples,
    violations,
)
from backend.domain.validation import (
    RuleEnforcement,
    ValidationContext,
    ValidationSeverity,
    classify_rule_evaluation,
)
from backend.domain.worldtime import At, Between, Direction, Relative

pytestmark = pytest.mark.unit

NAMES = {"mara": "Mara", "ivo": "Ivo", "guild": "The Guild", "ring": "The Ring", "rook": "Rook"}


def _people() -> World:
    return World(
        entries={
            "mara": WorldEntry("mara", "Character", "Mara"),
            "ivo": WorldEntry("ivo", "Character", "Ivo"),
            "rook": WorldEntry("rook", "Character", "Rook", frozenset({"ghost"})),
            "guild": WorldEntry("guild", "Faction", "The Guild"),
            "harbor": WorldEntry("harbor", "Location", "Harbor"),
            "mill": WorldEntry("mill", "Location", "Mill"),
        }
    )


def _broken(body: RuleBody, world: World) -> dict[str, tuple[bool, str]]:
    return {item.entity_id: (item.certain, item.when) for item in violations("r", body, world)}


# ------------------------------------------------------------------- ranges
AGE = RuleBody(
    applies_to=Selector(type="Character"),
    checks=[Check(kind="between", pattern=Pattern(predicate="AGE"), low=0, high=120)],
)


def test_a_value_must_be_a_number_inside_its_range() -> None:
    world = _people()
    world.facts = {
        "a1": WorldFact("a1", "mara", "AGE", value="34"),
        "a2": WorldFact("a2", "ivo", "AGE", value="130"),
        "a3": WorldFact("a3", "rook", "AGE", value="old"),
    }
    # No age at all is fine: a range says what a value may be, not that there is one.
    # A value that is not a number cannot be judged: it may break the rule, never surely.
    assert _broken(AGE, world) == {"ivo": (True, ""), "rook": (False, "")}
    assert read_rule(AGE, NAMES) == ("Every character must have a number from 0 to 120 for “age”.")
    only_low = AGE.model_copy(
        update={"checks": [Check(kind="between", pattern=Pattern(predicate="AGE"), low=18)]}
    )
    assert "a number of at least 18" in read_rule(only_low, NAMES)


@pytest.mark.parametrize(
    ("value", "found"),
    [
        ("64 years", {}),
        ("2.5 m", {}),
        ("300 years", {"mara": (True, "")}),
        ("1,200 leagues", {"mara": (True, "")}),
        ("-3", {"mara": (True, "")}),
        # Not read as a number: it may break the range, never for certain, so never blocks.
        ("about 64", {"mara": (False, "")}),
        ("3/4", {"mara": (False, "")}),
        ("12:30", {"mara": (False, "")}),
    ],
)
def test_a_range_reads_the_number_a_value_starts_with(value: str, found: dict) -> None:
    """A true age written "64 years" is inside 0 to 120, not a false alarm."""
    world = _people()
    world.facts = {"a1": WorldFact("a1", "mara", "AGE", value=value)}
    assert _broken(AGE, world) == found


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "between", "pattern": {"predicate": "AGE"}},
        {"kind": "between", "pattern": {"predicate": "AGE"}, "low": 9, "high": 1},
        {"kind": "between", "pattern": {"predicate": "AGE", "value": "3"}, "low": 1},
        {"kind": "has", "pattern": {"predicate": "AGE"}, "high": 3},
        {"kind": "implies", "pattern": {"predicate": "OWNS"}},
        {"kind": "has", "pattern": {"predicate": "OWNS"}, "if_has": {"predicate": "OWNS"}},
        {"kind": "not_both", "pattern": {"predicate": "OWNS"}},
        {"kind": "has", "pattern": {"predicate": "OWNS"}, "about_id": "guild"},
    ],
)
def test_a_check_must_have_exactly_the_parts_its_kind_needs(fields: dict) -> None:
    with pytest.raises(ValidationError):
        Check.model_validate(fields)


# ------------------------------------------------- implications and conflicts
def test_if_it_has_one_fact_it_must_also_have_another() -> None:
    body = RuleBody(
        applies_to=Selector(type="Character"),
        checks=[
            Check(
                kind="implies",
                if_has=Pattern(predicate="MEMBER_OF", other_id="guild"),
                pattern=Pattern(predicate="OWNS", other_id="ring"),
            )
        ],
    )
    world = _people()
    world.entries["ring"] = WorldEntry("ring", "Object", "The Ring")
    world.facts = {
        "m1": WorldFact("m1", "mara", "MEMBER_OF", object_id="guild"),
        "m2": WorldFact("m2", "ivo", "MEMBER_OF", object_id="guild"),
        "o1": WorldFact("o1", "ivo", "OWNS", object_id="ring"),
    }
    assert set(_broken(body, world)) == {"mara"}
    assert read_rule(body, NAMES) == (
        "Every character if it has this: it is a member of The Guild, must also have this: "
        "it owns The Ring."
    )


def test_an_entry_cannot_have_both_of_two_facts() -> None:
    body = RuleBody(
        applies_to=Selector(type="Character"),
        checks=[
            Check(
                kind="not_both",
                pattern=Pattern(predicate="HAS_STATUS", value="dead"),
                not_with=Pattern(predicate="LOCATED_AT"),
            )
        ],
    )
    world = _people()
    world.facts = {
        "s1": WorldFact("s1", "mara", "HAS_STATUS", value="Dead"),
        "l1": WorldFact("l1", "mara", "LOCATED_AT", object_id="harbor"),
        "s2": WorldFact("s2", "ivo", "HAS_STATUS", value="dead"),
    }
    assert set(_broken(body, world)) == {"mara"}
    assert "cannot have both this: it has the status “dead” and this: it is located at" in (
        read_rule(body, NAMES)
    )


# ------------------------------------------------------------------ exceptions
def test_named_entries_a_tag_or_a_fact_excuse_an_entry() -> None:
    body = RuleBody(
        applies_to=Selector(type="Character"),
        checks=[Check(kind="has", pattern=Pattern(predicate="LOCATED_AT"))],
        except_for=Exceptions(
            entity_ids=["ivo"],
            tag="Ghost",
            having=[Pattern(predicate="HAS_STATUS", value="lost")],
        ),
    )
    world = _people()
    world.entries["ana"] = WorldEntry("ana", "Character", "Ana")
    world.facts = {"s1": WorldFact("s1", "ana", "HAS_STATUS", value="lost")}
    assert set(_broken(body, world)) == {"mara"}
    assert body.form == 2
    assert read_rule(body, NAMES) == (
        "Every character, except for Ivo, anything tagged “Ghost” and anything where it has "
        "the status “lost”, must have this: it is located at something."
    )
    assert referenced_ids(body) == {"ivo"}
    problems = body_problems(
        body.model_copy(update={"except_for": Exceptions(entity_ids=["nobody"])}),
        predicates={"LOCATED_AT", "HAS_STATUS"},
        entity_types={"Character"},
        entity_ids=set(world.entries),
    )
    assert problems == ["An entry this rule leaves out is not in the story bible."]


# --------------------------------------------------------------------- beliefs
HONEST = RuleBody(
    applies_to=Selector(
        type="Character", having=[Pattern(predicate="MEMBER_OF", other_id="guild")]
    ),
    checks=[
        Check(
            kind="believes",
            about_id="guild",
            pattern=Pattern(predicate="HAS_DETAIL", value="honest"),
        )
    ],
)


def test_every_member_of_the_guild_believes_the_guild_is_honest() -> None:
    world = _people()
    world.facts = {
        "m1": WorldFact("m1", "mara", "MEMBER_OF", object_id="guild"),
        "m2": WorldFact("m2", "ivo", "MEMBER_OF", object_id="guild"),
        # The truth does not matter: a belief is what someone holds, right or wrong.
        "d1": WorldFact("d1", "guild", "HAS_DETAIL", value="corrupt"),
    }
    world.beliefs = {
        "b1": WorldBelief("b1", "mara", "guild", "HAS_DETAIL", value="Honest"),
        "b2": WorldBelief("b2", "rook", "guild", "HAS_DETAIL", value="honest"),
    }
    assert set(_broken(HONEST, world)) == {"ivo"}
    assert read_rule(HONEST, NAMES) == (
        "Every character where it is a member of The Guild, must believe this: The Guild has "
        "the detail “honest”."
    )
    assert referenced_ids(HONEST) == {"guild"}


def test_a_belief_about_oneself_and_one_that_must_not_be_held() -> None:
    body = RuleBody(
        applies_to=Selector(type="Character"),
        checks=[
            Check(
                kind="does_not_believe",
                pattern=Pattern(predicate="MEMBER_OF", other_id="guild"),
            )
        ],
    )
    world = _people()
    world.beliefs = {"b1": WorldBelief("b1", "ivo", "ivo", "MEMBER_OF", object_id="guild")}
    assert set(_broken(body, world)) == {"ivo"}
    assert "must not believe this: it is a member of The Guild" in read_rule(body, NAMES)
    # A belief about an entry that is gone does not count.
    del world.entries["guild"]
    assert _broken(body, world) == {}


def test_belief_changes_are_applied_and_reach_only_their_holder() -> None:
    world = _people()
    world.facts = {"m1": WorldFact("m1", "mara", "MEMBER_OF", object_id="guild")}
    honest = Proposition(
        subject_id="guild", predicate="HAS_DETAIL", object=StringObject(value="honest")
    )
    operations = [AssertBeliefOp(id="b9", holder_entity_id="mara", proposition=honest)]
    after = apply_operations(world, operations)
    assert _broken(HONEST, world) == {"mara": (True, "")}
    assert _broken(HONEST, after) == {}
    assert affected_entries(world, after, operations) == {"mara"}

    closed = apply_operations(after, [CloseBeliefOp(id="b9")])
    assert closed.beliefs == {} and set(after.beliefs) == {"b9"}
    assert affected_entries(after, closed, [CloseBeliefOp(id="b9")]) == {"mara"}
    # Removing what a belief is about reaches whoever holds it.
    assert "mara" in affected_entries(after, after, [RetireEntityOp(id="guild")])


# ------------------------------------------------------------------ story time
ONE_PLACE = RuleBody(
    applies_to=Selector(type="Character"),
    checks=[Check(kind="at_most", count=1, pattern=Pattern(predicate="LOCATED_AT"))],
    time="every_moment",
)


def _moved(world: World, first_end: object, second_start: object) -> World:
    world.facts = {
        "l1": WorldFact("l1", "mara", "LOCATED_AT", object_id="harbor", ends=first_end),
        "l2": WorldFact("l2", "mara", "LOCATED_AT", object_id="mill", starts=second_start),
    }
    return world


def test_at_every_moment_two_places_one_after_the_other_are_fine() -> None:
    world = _moved(_people(), At(at=5), At(at=5))
    assert _broken(ONE_PLACE, world) == {}
    # Over every fact as recorded, the same story breaks it: that is what form 1 meant.
    recorded = ONE_PLACE.model_copy(update={"time": "as_recorded"})
    assert set(_broken(recorded, world)) == {"mara"}
    assert read_rule(ONE_PLACE, NAMES).startswith("At every moment of the story: Every character")


def test_overlapping_spans_break_it_and_say_when() -> None:
    world = _moved(_people(), At(at=7), At(at=5))
    assert _broken(ONE_PLACE, world) == {"mara": (True, "at 5 in story time")}


def test_times_too_loose_to_tell_may_break_it_and_never_count_as_sure() -> None:
    # She leaves the harbour some time in years 4 to 8 and reaches the mill at 6.
    world = _moved(_people(), Between(earliest=4, latest=8), At(at=6))
    assert _broken(ONE_PLACE, world) == {"mara": (False, "around 6 in story time")}


def test_a_fact_timed_from_an_event_moves_with_the_event() -> None:
    world = _people()
    world.events = {"war": At(at=10)}
    world.facts = {
        "l1": WorldFact("l1", "mara", "LOCATED_AT", object_id="harbor", ends=At(at=12)),
        "l2": WorldFact(
            "l2",
            "mara",
            "LOCATED_AT",
            object_id="mill",
            starts=Relative(anchor_event_id="war", offset=1, direction=Direction.AFTER),
        ),
    }
    assert set(_broken(ONE_PLACE, world)) == {"mara"}
    operations = [AmendEventOp(id="war", when=At(at=20))]
    assert moves_time(operations)
    assert _broken(ONE_PLACE, apply_operations(world, operations)) == {}
    later = apply_operations(world, [AmendFactOp(id="l1", ends=At(at=11))])
    assert _broken(ONE_PLACE, later) == {}
    assert not moves_time([AmendFactOp(id="l1", ends=At(at=11))])
    assert moves_time([CreateEventOp(id="e2", kind="battle", name="Battle")])


def test_at_the_end_of_the_story_only_what_has_not_stopped_counts() -> None:
    body = RuleBody(
        applies_to=Selector(entity_id="mara"),
        checks=[Check(kind="lacks", pattern=Pattern(predicate="HAS_STATUS", value="cursed"))],
        time="at_end",
    )
    world = _people()
    world.facts = {"c1": WorldFact("c1", "mara", "HAS_STATUS", value="cursed", ends=At(at=9))}
    assert _broken(body, world) == {}
    world.facts["c1"] = WorldFact("c1", "mara", "HAS_STATUS", value="cursed", starts=At(at=9))
    assert _broken(body, world) == {"mara": (True, "at the end of the story")}
    # An end timed from an event that is not there may or may not have come.
    world.facts["c1"] = WorldFact(
        "c1", "mara", "HAS_STATUS", value="cursed", ends=Relative(anchor_event_id="gone")
    )
    assert _broken(body, world) == {"mara": (False, "at the end of the story")}
    assert read_rule(body, NAMES).startswith("At the end of the story: Mara must not have")


ONE_SPOUSE = RuleBody(
    applies_to=Selector(type="Character"),
    checks=[Check(kind="at_most", count=1, pattern=Pattern(predicate="MARRIED_TO"))],
    time="every_moment",
)


def _after_the_untimed_wedding() -> AssertFactOp:
    return AssertFactOp(
        id="s1",
        proposition=Proposition(
            subject_id="mara",
            predicate="MARRIED_TO",
            object=EntityRefObject(entity_id="ivo", project_id="p"),
        ),
        starts=Relative(anchor_event_id="wedding"),
    )


def test_a_start_that_cannot_be_placed_may_be_any_time_and_is_never_sure() -> None:
    """Married to Ivo from a wedding with no time, and to Rook until year 5."""
    world = _people()
    world.events = {"wedding": None}
    world.facts = {"s2": WorldFact("s2", "mara", "MARRIED_TO", object_id="rook", ends=At(at=5))}
    married = apply_operations(world, [_after_the_untimed_wedding()])
    # The wedding may come after year 5, so two spouses at once is only possible.
    assert _broken(ONE_SPOUSE, married) == {"mara": (False, "around 4 in story time")}
    (found,) = _evaluate(world, [_rule(ONE_SPOUSE)], [_after_the_untimed_wedding()])
    assert found.complete is False
    assert classify_rule_evaluation(found) is ValidationSeverity.ADVISORY
    # The same for an end: married to Rook until that wedding, and to Ivo from year 5.
    world.facts = {
        "s1": WorldFact("s1", "mara", "MARRIED_TO", object_id="ivo", starts=At(at=5)),
        "s2": WorldFact(
            "s2", "mara", "MARRIED_TO", object_id="rook", ends=Relative(anchor_event_id="wedding")
        ),
    }
    assert _broken(ONE_SPOUSE, world) == {"mara": (False, "around 5 in story time")}


def test_the_selector_is_judged_at_each_moment_too() -> None:
    """Every member must own a badge, only while a member."""
    body = RuleBody(
        applies_to=Selector(type="Character", having=[Pattern(predicate="MEMBER_OF")]),
        checks=[Check(kind="has", pattern=Pattern(predicate="OWNS"))],
        time="every_moment",
    )
    world = _people()
    world.entries["badge"] = WorldEntry("badge", "Object", "Badge")
    world.facts = {
        "m1": WorldFact("m1", "mara", "MEMBER_OF", object_id="guild", ends=At(at=3)),
        "o1": WorldFact("o1", "mara", "OWNS", object_id="badge", ends=At(at=3)),
    }
    assert _broken(body, world) == {}
    world.facts["o1"] = WorldFact("o1", "mara", "OWNS", object_id="badge", ends=At(at=2))
    assert _broken(body, world) == {"mara": (True, "at 2 in story time")}


# ---------------------------------------------------------- examples and form
def test_examples_carry_times_and_beliefs() -> None:
    entries = [
        ExampleEntry(ref="a", type="Character", name="Ana"),
        ExampleEntry(ref="h", type="Location", name="Home"),
        ExampleEntry(ref="t", type="Location", name="Town"),
    ]
    examples = [
        RuleExample(
            description="Ana moves house",
            entries=entries,
            facts=[
                ExampleFact(subject_ref="a", predicate="LOCATED_AT", object_ref="h", ends=3),
                ExampleFact(subject_ref="a", predicate="LOCATED_AT", object_ref="t", starts=3),
            ],
            expect="allowed",
        ),
        RuleExample(
            description="Ana in two towns at once",
            entries=entries,
            facts=[
                ExampleFact(subject_ref="a", predicate="LOCATED_AT", object_ref="h"),
                ExampleFact(subject_ref="a", predicate="LOCATED_AT", object_ref="t", starts=3),
            ],
            expect="caught",
        ),
    ]
    assert all(item.agrees for item in run_examples(ONE_PLACE, examples))

    guild = [
        ExampleEntry(ref="g", type="Faction", name="Guild"),
        ExampleEntry(ref="a", type="Character", name="Ana"),
    ]
    member = ExampleFact(subject_ref="a", predicate="MEMBER_OF", object_ref="g")
    honest = RuleBody(
        applies_to=Selector(type="Character", having=[Pattern(predicate="MEMBER_OF")]),
        checks=[Check(kind="believes", about_id="g", pattern=Pattern(predicate="HAS_DETAIL"))],
    )
    results = run_examples(
        honest,
        [
            RuleExample(
                description="Doubting member", entries=guild, facts=[member], expect="caught"
            ),
            RuleExample(
                description="Loyal member",
                entries=guild,
                facts=[member],
                beliefs=[
                    ExampleBelief(
                        holder_ref="a", subject_ref="g", predicate="HAS_DETAIL", value="honest"
                    )
                ],
                expect="allowed",
            ),
        ],
    )
    assert [item.agrees for item in results] == [True, True]


def test_the_form_of_a_rule_is_kept_and_a_newer_form_is_refused() -> None:
    old = RuleBody.model_validate(
        {
            "applies_to": {"type": "Character"},
            "checks": [{"kind": "has", "pattern": {"predicate": "OWNS"}}],
        }
    )
    assert old.form == 1
    assert AGE.form == 2 and ONE_PLACE.form == 2 and HONEST.form == 2
    # Saying form 2 for a rule form 1 could say keeps 2: a kept rule means what it said.
    assert RuleBody.model_validate({**old.model_dump(), "form": 2}).form == 2
    with pytest.raises(ValidationError, match="newer version"):
        RuleBody.model_validate({**old.model_dump(), "form": RULE_FORM + 1})


# ---------------------------------------------------------------- commit time
def _rule(body: RuleBody, *, blocking: bool = True) -> StoryRule:
    now = datetime.now(UTC)
    return StoryRule(
        workspace_id="w",
        project_id="p",
        id="r1",
        title="One place at a time",
        sentence="",
        status=RuleStatus.ON,
        version=3,
        body=body,
        examples=[],
        created_by=None,
        created_at=now,
        updated_at=now,
        enforcement=RuleEnforcement.BLOCKING if blocking else RuleEnforcement.WARNING,
    )


def _evaluate(world: World, rules: list[StoryRule], operations: list) -> list:
    from backend.services.rule_service import RuleService

    class _Store:
        async def list_rules(self, *, workspace_id: str, project_id: str):
            return rules

    class _Service(RuleService):
        async def _world_for(self, context, rules):
            return world, True

    context = ValidationContext(
        workspace_id="w",
        project_id="p",
        branch_id="b",
        schema_version_id=None,
        head_revision=1,
        reader=object(),  # type: ignore[arg-type]
    )
    service = _Service(_Store(), reader=None)  # type: ignore[arg-type]
    return list(asyncio.run(service.evaluate(operations, context=context)))


def _at_mill(starts: object) -> AssertFactOp:
    return AssertFactOp(
        id="l2",
        proposition=Proposition(
            subject_id="mara",
            predicate="LOCATED_AT",
            object=EntityRefObject(entity_id="mill", project_id="p"),
        ),
        starts=starts,  # type: ignore[arg-type]
    )


def test_a_sure_break_in_story_time_blocks_and_a_loose_one_only_warns() -> None:
    world = _people()
    world.facts = {
        "l1": WorldFact(
            "l1", "mara", "LOCATED_AT", object_id="harbor", ends=Between(earliest=4, latest=8)
        )
    }
    (sure,) = _evaluate(world, [_rule(ONE_PLACE)], [_at_mill(At(at=2))])
    assert classify_rule_evaluation(sure) is ValidationSeverity.BLOCKING_RULE
    assert "Mara at 2 in story time" in sure.message and sure.rule_version == 3

    (loose,) = _evaluate(world, [_rule(ONE_PLACE)], [_at_mill(At(at=6))])
    assert loose.complete is False
    assert classify_rule_evaluation(loose) is ValidationSeverity.ADVISORY
    assert "too loose to be sure, so it warns instead of blocking" in loose.message

    assert _evaluate(world, [_rule(ONE_PLACE)], [_at_mill(At(at=9))]) == []


def test_making_a_possible_break_a_sure_one_blocks() -> None:
    # She may still be at the harbour when she reaches the mill: only possibly broken.
    world = _moved(_people(), Between(earliest=4, latest=8), At(at=6))
    assert _broken(ONE_PLACE, world) == {"mara": (False, "around 6 in story time")}
    # Reaching the mill at 2 makes it sure, and that is this change's doing.
    (found,) = _evaluate(world, [_rule(ONE_PLACE)], [AmendFactOp(id="l2", starts=At(at=2))])
    assert classify_rule_evaluation(found) is ValidationSeverity.BLOCKING_RULE
    assert "Mara at 2 in story time" in found.message
    # A change that leaves it possible, or sure as it was, is not blamed.
    assert _evaluate(world, [_rule(ONE_PLACE)], [AmendFactOp(id="l2", starts=At(at=7))]) == []
    sure = _moved(_people(), At(at=7), At(at=5))
    assert _evaluate(sure, [_rule(ONE_PLACE)], [AmendFactOp(id="l2", starts=At(at=4))]) == []


def test_moving_an_event_rechecks_everyone_a_rule_in_story_time_is_about() -> None:
    world = _people()
    world.events = {"war": At(at=20)}
    world.facts = {
        "l1": WorldFact("l1", "ivo", "LOCATED_AT", object_id="harbor", ends=At(at=12)),
        "l2": WorldFact(
            "l2", "ivo", "LOCATED_AT", object_id="mill", starts=Relative(anchor_event_id="war")
        ),
    }
    (found,) = _evaluate(world, [_rule(ONE_PLACE)], [AmendEventOp(id="war", when=At(at=10))])
    assert "Ivo at 10 in story time" in found.message


def test_a_belief_rule_is_checked_when_a_belief_ends() -> None:
    world = _people()
    world.facts = {"m1": WorldFact("m1", "mara", "MEMBER_OF", object_id="guild")}
    world.beliefs = {"b1": WorldBelief("b1", "mara", "guild", "HAS_DETAIL", value="honest")}
    (found,) = _evaluate(world, [_rule(HONEST, blocking=False)], [CloseBeliefOp(id="b1")])
    assert "would no longer hold for Mara" in found.message
    assert classify_rule_evaluation(found) is ValidationSeverity.ADVISORY


# ------------------------------------------------------------------ performance
def test_rules_in_story_time_and_belief_over_a_large_story_are_quick() -> None:
    """Two thousand entries, ten thousand timed facts, two thousand beliefs.

    Every moment of story time is worked out per entry from its own facts' edges
    through the FactIndex, so the whole story is checked in well under the bound.
    """
    world = World()
    for number in range(1000):
        world.entries[f"c{number}"] = WorldEntry(f"c{number}", "Character", f"C{number}")
        world.entries[f"l{number}"] = WorldEntry(f"l{number}", "Location", f"L{number}")
    for number in range(10_000):
        who = f"c{number % 1000}"
        start = (number // 1000) * 10
        world.facts[f"f{number}"] = WorldFact(
            f"f{number}",
            who,
            "LOCATED_AT",
            object_id=f"l{(number * 7) % 1000}",
            starts=At(at=start),
            ends=At(at=start + 10 + (number % 3 == 0)),
        )
    for number in range(2000):
        world.beliefs[f"b{number}"] = WorldBelief(
            f"b{number}", f"c{number % 1000}", f"l{number % 1000}", "HAS_DETAIL", value="safe"
        )
    bodies = [
        ONE_PLACE,
        ONE_PLACE.model_copy(update={"time": "at_end"}),
        RuleBody(
            applies_to=Selector(type="Character"),
            checks=[
                Check(
                    kind="implies",
                    if_has=Pattern(predicate="LOCATED_AT", other_id="l1"),
                    pattern=Pattern(predicate="LOCATED_AT", other_id="l2"),
                ),
                Check(kind="believes", pattern=Pattern(predicate="HAS_DETAIL"), about_id="l3"),
            ],
            mode="any",
            except_for=Exceptions(entity_ids=["c1"]),
            time="every_moment",
        ),
    ]
    started = time.perf_counter()
    index = FactIndex(world)
    found = [violations("r", body, world, index=index, limit=None) for body in bodies]
    elapsed = time.perf_counter() - started
    assert elapsed < 10.0, f"checking rules in story time took {elapsed:.1f}s"
    # Every third fact overlaps the next by one year: those people are in two places.
    assert found[0] and all(item.certain for item in found[0])
    assert found[1] == []
