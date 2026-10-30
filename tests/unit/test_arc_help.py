"""Arc stages and gap bridges as pure functions (STY-006, STY-004)."""

from __future__ import annotations

import pytest

from backend.domain.arcs import ArcStage, ScaleSet, StateScale, arc_path, with_path
from backend.domain.canon.plans import PlanKind
from backend.services.arc_help import (
    StageDraft,
    StageSheet,
    bridge_id,
    stage_change,
    stage_gap,
    thread_gap,
)
from tests.unit.test_arcs import _arc, _plan

pytestmark = pytest.mark.unit

MATERIAL = "What it is about: Mara stops working alone.\n- s1: “One” — Mara and Tobin fall out."
SCALES = ScaleSet(scales=[StateScale(id="trust", name="Trust", levels=["none", "some", "full"])])


def _change(*drafts: StageDraft, arc=None):
    return stage_change(
        StageSheet(stages=list(drafts)),
        arc or _plan("arc", PlanKind.ARC),
        material=MATERIAL,
        scene_order=["s1", "s2"],
        fact_ids={"fct_1"},
        scales=SCALES,
        job_id="job_1",
    )


def test_only_shown_stages_in_order_with_real_parts_are_kept() -> None:
    change = _change(
        StageDraft(name="Alone", scene_id="s2", words="Mara stops working alone"),
        StageDraft(name="Angry", scene_id="s2", words="Mara and Tobin fall out"),
        StageDraft(
            name="Trusting",
            scene_id="s1",
            fact_id="fct_9",
            scale_id="trust",
            level="FULL",
            words="Mara and Tobin fall out",
        ),
        StageDraft(name="angry", words="Mara and Tobin fall out"),  # same name: dropped
        StageDraft(name="Gone", words="two words"),  # too short: dropped
    )
    assert change.operation is not None and change.stages == 3 and change.dropped == 2
    stages = arc_path(change.operation.details)
    # The first stage needs no scene; a scene before an earlier stage's is not kept.
    assert [s.scene_id for s in stages] == [None, "s2", None]
    assert stages[2].fact_id is None and (stages[2].scale_id, stages[2].level) == ("trust", "full")
    assert change.cleared == 2


def test_an_arc_with_stages_or_an_answer_of_one_stage_changes_nothing() -> None:
    kept = _arc(ArcStage(name="Already"))
    assert (
        _change(StageDraft(name="A", words="Mara stops working alone"), arc=kept).operation is None
    )
    one = _change(StageDraft(name="A", words="Mara stops working alone"))
    assert one.operation is None


def test_a_thread_gap_goes_before_the_scene_that_resolves_it() -> None:
    thread = _plan("t", PlanKind.THREAD)
    items = [
        thread,
        _plan("s1", PlanKind.SCENE, 0, sets_up=["t"]),
        _plan("s2", PlanKind.SCENE, 1),
        _plan("s3", PlanKind.SCENE, 2, pays_off=["t"]),
    ]
    gap = thread_gap(items, "t")
    assert not isinstance(gap, str) and gap.anchor.id == "s3" and not gap.after
    moved = [*items[:2], items[2].model_copy(update={"details": {"_advances": '["t"]'}}), items[3]]
    assert isinstance(thread_gap(moved, "t"), str)
    assert isinstance(thread_gap(items, "s1"), str)


def test_a_stage_gap_goes_after_the_stage_before_or_before_the_stage_after() -> None:
    items = [_plan("s1", PlanKind.SCENE, 0), _plan("s2", PlanKind.SCENE, 1)]
    arc = _plan(
        "arc",
        PlanKind.ARC,
        details=with_path(
            {}, [ArcStage(name="A"), ArcStage(name="B"), ArcStage(name="C", scene_id="s2")]
        ),
    )
    gap = stage_gap([*items, arc], "arc", 1, {})
    assert not isinstance(gap, str) and gap.anchor.id == "s2" and not gap.after
    assert "goes from “A” to “B”, then “C”" in gap.words
    assert isinstance(stage_gap([*items, arc], "arc", 2, {}), str)  # already placed
    assert isinstance(stage_gap([*items, arc], "arc", 0, {}), str)  # where it starts
    lonely = arc.model_copy(
        update={"details": with_path({}, [ArcStage(name="A"), ArcStage(name="B")])}
    )
    assert "Place another stage" in str(stage_gap([*items, lonely], "arc", 1, {}))
    assert bridge_id("job_1") == bridge_id("job_1") != bridge_id("job_2")
