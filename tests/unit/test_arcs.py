"""Character arc paths as pure functions (STY-006)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.domain.arcs import (
    MAX_STAGES,
    ArcStage,
    arc_findings,
    arc_path,
    long_stretch,
    moves_in_scene,
    path_fits,
    read_arcs,
    with_path,
    without_scenes,
)
from backend.domain.canon.plans import PlanItem, PlanKind, PlanRef
from backend.domain.knowledge import reading_order

pytestmark = pytest.mark.unit


def _plan(id_: str, kind: PlanKind, position: int = 0, **fields) -> PlanItem:
    return PlanItem(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        kind=kind,
        title=id_,
        position=position,
        provenance_id="prov",
        created_revision=1,
        **fields,
    )


def _arc(*stages: ArcStage, about: str = "mara") -> PlanItem:
    return _plan(
        "arc",
        PlanKind.ARC,
        refs=[PlanRef(entity_id=about, role="about")],
        details=with_path({"stakes": "high"}, stages),
    )


def _scenes(count: int) -> list[PlanItem]:
    return [_plan(f"s{n}", PlanKind.SCENE, n) for n in range(1, count + 1)]


def _codes(items: list[PlanItem], fact_standing=None) -> list[tuple[str, str]]:
    scenes = reading_order(items)
    titles = {item.id: item.title for item in items}
    readings = read_arcs(items, scenes, fact_standing)
    return [(f.code, f.kind) for f in arc_findings(readings, scenes, titles)]


def test_a_path_round_trips_and_keeps_the_arcs_own_notes() -> None:
    stages = [ArcStage(name="  Trusts   no one "), ArcStage(name="Trusts Tobin", scene_id="s2")]
    details = with_path({"stakes": "high"}, stages)
    assert details["stakes"] == "high"
    assert [s.name for s in arc_path(details)] == ["Trusts no one", "Trusts Tobin"]
    assert with_path(details, []) == {"stakes": "high"}
    assert arc_path({"_path": "not json"}) == []
    assert arc_path({"_path": '[{"name": ""}, {"name": "Ok"}]'}) == [ArcStage(name="Ok")]
    with pytest.raises(ValidationError):
        ArcStage(name="   ")


def test_the_longest_path_fits_in_one_detail_even_with_quotes_and_accents() -> None:
    stages = [
        ArcStage(name=('é"' * 30)[:60], scene_id="pln_" + "x" * 36, fact_id="fct_" + "y" * 36)
        for _ in range(MAX_STAGES)
    ]
    assert path_fits(stages)


def test_stages_in_order_with_no_long_stretch_need_nothing() -> None:
    items = [
        *_scenes(4),
        _arc(
            ArcStage(name="Trusts no one"),
            ArcStage(name="Doubts", scene_id="s2"),
            ArcStage(name="Trusts Tobin", scene_id="s4"),
        ),
    ]
    assert _codes(items) == []


def test_a_stage_reached_before_the_previous_one_is_out_of_order() -> None:
    items = [
        *_scenes(3),
        _arc(
            ArcStage(name="Trusts no one", scene_id="s1"),
            ArcStage(name="Doubts", scene_id="s3"),
            ArcStage(name="Trusts Tobin", scene_id="s2"),
        ),
    ]
    scenes = reading_order(items)
    found = arc_findings(read_arcs(items, scenes), scenes, {i.id: i.title for i in items})
    assert [(f.plan_id, f.code, f.kind) for f in found] == [("arc", "arc_out_of_order", "problem")]
    assert found[0].message == (
        "“Trusts Tobin” is reached in “s2”, which comes before “s3”, where the earlier "
        "stage “Doubts” is reached."
    )


def test_two_stages_in_one_scene_are_allowed() -> None:
    items = [
        *_scenes(2),
        _arc(ArcStage(name="A", scene_id="s1"), ArcStage(name="B", scene_id="s1")),
    ]
    assert _codes(items) == []


def test_unplaced_stages_after_the_first_are_gaps_and_the_start_needs_no_scene() -> None:
    items = [*_scenes(2), _arc(ArcStage(name="Start"), ArcStage(name="End"))]
    assert _codes(items) == [("arc_stage_not_placed", "gap")]


def test_a_long_stretch_is_a_quarter_of_the_scenes_and_at_least_four() -> None:
    assert long_stretch(4) == 4
    assert long_stretch(40) == 10
    # Five scenes between stages in a plan of ten: more than four, so a note.
    items = [
        *_scenes(10),
        _arc(ArcStage(name="A", scene_id="s1"), ArcStage(name="B", scene_id="s7")),
    ]
    assert _codes(items) == [("arc_long_stretch", "gap")]
    scenes = reading_order(items)
    found = arc_findings(read_arcs(items, scenes), scenes, {"arc": "Learning to trust"})
    assert found[0].message.startswith("Nothing moves “Learning to trust” for 5 scenes")
    near = [
        *_scenes(10),
        _arc(ArcStage(name="A", scene_id="s1"), ArcStage(name="B", scene_id="s6")),
    ]
    assert _codes(near) == []


def test_a_stage_whose_fact_is_not_true_then_or_gone_is_flagged() -> None:
    items = [
        *_scenes(2),
        _arc(
            ArcStage(name="Trusts no one", fact_id="f_alone"),
            ArcStage(name="Trusts Tobin", scene_id="s2", fact_id="f_trust"),
        ),
    ]
    standing = {"f_alone": "gone", "f_trust": "not_then"}
    seen: list[tuple[str, str | None]] = []

    def judge(fact_id: str, scene: PlanItem | None):
        seen.append((fact_id, scene.id if scene else None))
        return standing[fact_id]

    assert _codes(items, judge) == [
        ("arc_fact_gone", "problem"),
        ("arc_fact_not_true", "problem"),
    ]
    assert seen == [("f_alone", None), ("f_trust", "s2")]
    assert _codes(items, lambda _f, _s: "unclear") == []


def test_a_stage_in_a_removed_scene_is_flagged_and_removal_unplaces_it() -> None:
    arc = _arc(ArcStage(name="A"), ArcStage(name="B", scene_id="gone"))
    assert _codes([*_scenes(1), arc]) == [("arc_stage_scene_gone", "problem")]
    cleaned = without_scenes(arc.details, {"gone"})
    assert arc_path(cleaned)[1].scene_id is None
    assert cleaned["stakes"] == "high"
    assert without_scenes(arc.details, {"other"}) is arc.details


def test_a_scene_that_moves_an_arc_knows_the_stage_going_in_and_the_one_reached() -> None:
    items = [
        *_scenes(3),
        _arc(
            ArcStage(name="Trusts no one"),
            ArcStage(name="Trusts Tobin", scene_id="s2", fact_id="f"),
        ),
    ]
    [move] = moves_in_scene(items, "s2")
    assert (move.current, move.reached, move.about, move.fact_id) == (
        "Trusts no one",
        "Trusts Tobin",
        ["mara"],
        "f",
    )
    assert moves_in_scene(items, "s1") == []
