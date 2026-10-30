"""The scratch copy a change is previewed on (CON-017): the change applied the way history
replays it, a merge expanded first, the story itself never touched; and how conditions
and the reader's guide differ, in words."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.domain.canon.assertions import Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.impact import condition_changes, reader_changes
from backend.domain.operations import parse_operation
from backend.services.impact_service import after_change
from backend.services.scratch_story import scratch_story
from backend.services.story_at_version import StoryAt
from backend.services.story_time_service import Story

pytestmark = pytest.mark.unit


def _entity(entity_id: str, name: str) -> CanonEntity:
    return CanonEntity(
        id=entity_id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        type="Character",
        name=name,
        schema_version_id="s",
        created_revision=1,
    )


def _fact(fact_id: str, subject: str, other: str) -> Fact:
    return Fact(
        id=fact_id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        proposition=Proposition(
            subject_id=subject,
            predicate="KNOWS",
            object=EntityRefObject(entity_id=other, project_id="p"),
        ),
        provenance_id="prv",
        created_revision=1,
    )


def _scene(plan_id: str, title: str, reveals: str, position: int = 0) -> PlanItem:
    return PlanItem(
        id=plan_id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        kind=PlanKind.SCENE,
        title=title,
        position=position,
        details={"_reveals": reveals},
        provenance_id="prv",
        created_revision=1,
    )


def _state() -> StoryAt:
    return StoryAt(
        story=Story(
            entities={
                "a": _entity("a", "Mara"),
                "b": _entity("b", "The Fox"),
                "c": _entity("c", "Tobin"),
            },
            facts=[_fact("f1", "b", "c")],
            events=[],
        ),
        beliefs=[],
        plan_items=[_scene("s1", "The Docks", '[{"fact_id": "f1"}]')],
    )


def test_a_merge_is_previewed_on_a_copy_and_the_story_is_left_as_it_was() -> None:
    state = _state()
    merge = parse_operation({"op": "merge_entities", "id": "b", "into": "a"})
    copy = asyncio.run(
        scratch_story(state, [merge], workspace_id="w", project_id="p", branch_id="b", revision=2)
    )
    # The merged entry is gone from the copy and its fact is stated again about Mara.
    assert "b" not in copy.story.entities and "b" in state.story.entities
    assert [fact.id for fact in state.story.facts] == ["f1"]
    (moved,) = copy.story.facts
    assert moved.proposition.subject_id == "a" and moved.id != "f1"
    found = after_change(state, copy)
    assert [item.words for item in found] == [
        "The reader would no longer learn: The Fox knows Tobin"
    ]


def test_conditions_say_how_they_would_stand_and_which_are_new_or_gone() -> None:
    def state(plan_id: str, reading: str, status: str, when: str = "before") -> SimpleNamespace:
        return SimpleNamespace(plan_id=plan_id, when=when, reading=reading, status=status)

    before = [state("s1", "Mara is at the harbor", "met"), state("s1", "Old", "unclear")]
    after = [
        state("s1", "Mara is at the harbor", "planned"),
        state("s2", "Tobin is free", "not_met", "after"),
    ]
    found = condition_changes(before, after, {"s1": "The Docks", "s2": "The Quay"})
    assert [(item.title, item.words) for item in found] == [
        (
            "The Docks",
            "“Mara is at the harbor” (before the scene) would be planned in an earlier scene "
            "instead of met",
        ),
        ("The Docks", "The condition “Old” (before the scene) would no longer be in the plan"),
        ("The Quay", "A new condition “Tobin is free” (after the scene) would not be met"),
    ]
    assert found[0].now == "met" and found[0].after == "planned in an earlier scene"


def test_the_guide_says_what_the_reader_and_the_viewpoint_would_know_differently() -> None:
    def step(plan_id: str, number: int, **fields) -> SimpleNamespace:
        values = {
            "shown": [],
            "irony": [],
            "withheld": [],
            "repeated": [],
            "flashback": False,
            **fields,
        }
        return SimpleNamespace(
            scene=SimpleNamespace(id=plan_id, title=plan_id.upper()), number=number, **values
        )

    def reveal(fact_id: str, how: str = "shown") -> SimpleNamespace:
        return SimpleNamespace(fact_id=fact_id, how=how)

    before = [
        step("s1", 1, shown=[reveal("f1"), reveal("f2")], withheld=["f3"]),
        step("s2", 2),
    ]
    after = [
        step("s1", 1, shown=[reveal("f1", "hinted")], irony=["f1"], flashback=True),
        step("s3", 2),
    ]
    found = [item.words for item in reader_changes(before, after, str.upper, str.upper)]
    assert found == [
        "The reader would be given a hint of instead: F1",
        "The reader would no longer learn: F2",
        "The reader would know, and the viewpoint character not: F1",
        "No longer kept from the reader: F3",
        "It would be a flashback",
        "It would come into the reading order",
        "It would leave the reading order",
    ]


def test_the_scratch_copy_is_made_and_read_off_the_event_loop(monkeypatch) -> None:
    """Expanding a merge and applying the change copy every record: for a large story
    that is long work, so none of it runs on the event loop (the PERFORMANCE RULE)."""
    import backend.services.impact_service as impact_module
    from backend.domain.impact import Impact
    from backend.services.impact_service import ImpactService

    real = impact_module.scratch_story
    on_loop: list[bool] = []

    async def watched(*args, **kwargs):
        loop = asyncio.get_running_loop()
        on_loop.append(loop is outer)
        return await real(*args, **kwargs)

    monkeypatch.setattr(impact_module, "scratch_story", watched)
    service = ImpactService(reader=SimpleNamespace(), mutation=None, journal=None)
    impact = Impact(about=[], affected=[], may_be_affected=[], not_checked=[])
    merge = parse_operation({"op": "merge_entities", "id": "b", "into": "a"})
    outer = None

    async def run() -> None:
        nonlocal outer
        outer = asyncio.get_running_loop()
        await service._after_change(  # noqa: SLF001
            impact, _state(), [merge], workspace_id="w", project_id="p", branch_id="b"
        )

    asyncio.run(run())
    assert on_loop == [False]
    assert impact.scratch is True
    assert [item.words for item in impact.after_change] == [
        "The reader would no longer learn: The Fox knows Tobin"
    ]
