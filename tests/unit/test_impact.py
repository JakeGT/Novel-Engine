"""The dependency index behind "What this change touches" (CON-007, CON-017): pure and exact."""

from datetime import UTC, datetime

import pytest

from backend.domain.canon.plans import PlanItem, PlanKind, PlanRef
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.impact import (
    MAX_DEPENDENTS,
    Dependent,
    KeptDraft,
    Mention,
    StoryFacts,
    Touched,
    assemble,
    draft_dependents,
    own_text_dependents,
    plan_dependents,
    rule_dependents,
    standing_gaps,
    text_dependents,
    touched_by,
    touched_entry,
)
from backend.domain.operations import (
    AssertFactOp,
    CreateEntityOp,
    CreatePlanItemOp,
    UpdateEntityOp,
    UpdatePlanItemOp,
)
from backend.domain.rules import (
    Check,
    Pattern,
    RuleBody,
    RuleStatus,
    Selector,
    StoryRule,
    World,
    WorldEntry,
    WorldFact,
    apply_operations,
)
from backend.domain.threads import with_advances
from backend.domain.validation import RuleEnforcement

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _plan(id: str, kind: PlanKind, title: str, **fields) -> PlanItem:
    return PlanItem(
        id=id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        kind=kind,
        title=title,
        provenance_id="prov",
        created_revision=1,
        **fields,
    )


def _rule(id: str, body: RuleBody, **fields) -> StoryRule:
    return StoryRule(
        workspace_id="w",
        project_id="p",
        id=id,
        title=fields.pop("title", id),
        sentence="",
        status=fields.pop("status", RuleStatus.ON),
        version=1,
        body=body,
        examples=[],
        created_by=None,
        created_at=NOW,
        updated_at=NOW,
        **fields,
    )


def _story(**plans: PlanItem) -> StoryFacts:
    return StoryFacts(
        names={"mara": "Mara", "harbor": "Lantern Harbor", "tobin": "Tobin"},
        facts={"f1": WorldFact("f1", "mara", "LOCATED_AT", "harbor")},
        plans=plans,
    )


def test_a_change_names_existing_entries_and_counts_new_ones_apart():
    story = _story()
    touched = touched_by(
        [
            CreateEntityOp(id="tmp:ines", type="Character", name="Ines"),
            UpdateEntityOp(id="mara", name="Mara Venn"),
            AssertFactOp(
                id="tmp:f",
                proposition=Proposition(
                    subject_id="tmp:ines",
                    predicate="KNOWS",
                    object=EntityRefObject(entity_id="tobin", project_id="p"),
                ),
            ),
        ],
        story,
    )
    assert touched.entries == {"mara": "Mara", "tobin": "Tobin"}
    assert touched.new_entries == 1
    assert touched.about == ["Ines is added", "Mara is changed", "New fact: Ines knows Tobin"]
    assert "Writing is not searched for new entries until they are added." in standing_gaps(touched)


def test_scenes_name_an_entry_by_role_and_a_changed_thread_reaches_what_carries_it():
    thread = _plan("t1", PlanKind.THREAD, "Where is Mara?")
    opens = _plan("s1", PlanKind.SCENE, "Docks", sets_up=["t1"])
    moves = _plan("s2", PlanKind.SCENE, "Quay", details=with_advances({}, ["t1"]))
    gone = _plan("s3", PlanKind.SCENE, "Cut", retired_revision=4, sets_up=["t1"])
    pov = _plan(
        "s4", PlanKind.SCENE, "Night", refs=[PlanRef(entity_id="mara", role="point_of_view")]
    )
    story = _story(t1=thread, s1=opens, s2=moves, s3=gone, s4=pov)

    touched = Touched(entries={"mara": "Mara"}, plans={"t1": "Where is Mara?"})
    found = {item.title: item for item in plan_dependents(touched, story)}
    assert set(found) == {"Docks", "Quay", "Night"}  # not the thread itself, not a removed scene
    assert found["Docks"].reasons == ["It opens “Where is Mara?”"]
    assert found["Quay"].reasons == ["It moves along “Where is Mara?”"]
    assert found["Night"].reasons == ["Mara is the point of view"]
    assert all(item.level == "affected" for item in found.values())


def test_a_thread_is_touched_by_the_parts_of_the_plan_that_change_around_it():
    thread = _plan("t1", PlanKind.THREAD, "Where is Mara?")
    docks = _plan("s1", PlanKind.SCENE, "Docks", sets_up=["t1"])
    story = _story(t1=thread, s1=docks)
    touched = touched_by(
        [
            UpdatePlanItemOp(id="s1", title="The Docks"),
            CreatePlanItemOp(id="tmp:s2", kind=PlanKind.SCENE, title="Quay", pays_off=["t1"]),
        ],
        story,
    )
    (found,) = plan_dependents(touched, story)
    assert found.kind == "thread" and found.title == "Where is Mara?"
    assert found.reasons == [
        "“Quay”, which is added, resolves it",
        "“Docks”, which changes, opens it",
    ]


def test_a_rule_is_listed_when_it_is_about_an_entry_before_or_after_the_change():
    before = World(entries={"mara": WorldEntry("mara", "Character", "Mara")})
    tagged = UpdateEntityOp(id="mara", attributes={"_tags": ["pilot"]})
    after = apply_operations(before, [tagged])
    pilots = RuleBody(
        applies_to=Selector(tag="pilot"),
        checks=[Check(kind="has", pattern=Pattern(predicate="LOCATED_AT"))],
    )
    naming = RuleBody(
        applies_to=Selector(type="Location"),
        checks=[Check(kind="lacks", pattern=Pattern(predicate="HOME_OF", other_id="mara"))],
    )
    rules = [
        _rule("r1", pilots, title="Pilots are somewhere", enforcement=RuleEnforcement.BLOCKING),
        _rule("r2", naming, title="Nobody lives here", status=RuleStatus.PAUSED),
        _rule("r3", RuleBody(applies_to=Selector(type="Location"), checks=pilots.checks)),
    ]
    found = {
        item.title: item
        for item in rule_dependents(touched_entry("mara", _story()), rules, before, after)
    }
    assert set(found) == {"Pilots are somewhere", "Nobody lives here"}
    assert found["Pilots are somewhere"].reasons == [
        "It applies to Mara",
        "It stops a change that breaks it",
    ]
    assert found["Nobody lives here"].reasons == [
        "It names Mara",
        "It is paused, so it is not checked now",
    ]


def test_kept_drafts_are_grouped_by_scene_and_count_facts_about_what_changes():
    story = _story()
    touched = touched_entry("mara", story)
    drafts = [
        KeptDraft("s1", "Docks", "v2", 2, frozenset({"mara"})),
        KeptDraft("s1", "Docks", "v4", 4, frozenset({"f1"})),
        KeptDraft("s2", "Elsewhere", "v1", 1, frozenset({"tobin"})),
    ]
    (found,) = draft_dependents(touched, drafts, story)
    assert found.kind == "draft" and found.plan_id == "s1" and found.version_id == "v4"
    assert found.reasons == [
        "Version 4 was drafted with Mara is located at Lantern Harbor in what was given",
        "Version 2 was drafted with Mara in what was given",
    ]


def test_writing_that_names_an_entry_may_be_affected_and_a_scenes_own_text_is_merged():
    touched = Touched(entries={"mara": "Mara", "tobin": "Tobin"}, plans={"s1": "Docks"})
    mentions = [
        Mention("src1", "Docks", "v1", 0, 40, ("mara",), plan_id="s1"),
        Mention("src1", "Docks", "v1", 41, 90, ("tobin", "mara"), plan_id="s1"),
        Mention("src2", "Chapter one", "v9", 5, 50, ("harbor",)),
    ]
    texts = [
        *text_dependents(touched, mentions),
        *own_text_dependents(touched, {"s1": ("src1", "Docks")}),
    ]
    impact = assemble(touched, plan=[], rules=[], drafts=[], texts=texts, not_checked=["gap"])
    (found,) = impact.may_be_affected
    assert found.kind == "scene_text" and found.start == 0 and found.count == 2
    assert found.reasons == [
        "Mentions Mara and Tobin by name in 2 passages",
        "It was written to the scene’s plan as it was before this change",
    ]
    assert impact.not_checked == ["gap"]


def test_a_long_answer_says_it_stopped():
    many = [
        Dependent(kind="scene", level="affected", id=f"s{n}", title=f"Scene {n}")
        for n in range(MAX_DEPENDENTS + 1)
    ]
    impact = assemble(Touched(), plan=many, rules=[], drafts=[], texts=[], not_checked=[])
    assert len(impact.affected) == MAX_DEPENDENTS
    assert impact.not_checked == [
        f"Only the first {MAX_DEPENDENTS} of each are listed; there are more than that."
    ]
