"""Shared scales, progress along them, and facts that lapse between stages (STY-006)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.domain.arcs import (
    ArcStage,
    ScaleSet,
    StateScale,
    lapse_findings,
    read_arcs,
    read_scales,
    scale_findings,
    scale_progress,
    scale_removal_problem,
)
from backend.domain.knowledge import reading_order
from tests.unit.test_arcs import _arc, _scenes

pytestmark = pytest.mark.unit

TRUST = StateScale(id="trust", name="Trust", levels=["None", "Wary", "Some", "Complete"])


def _readings(*stages: ArcStage, scenes: int = 4):
    items = [*_scenes(scenes), _arc(*stages)]
    ordered = reading_order(items)
    return read_arcs(items, ordered), ordered, {item.id: item.title for item in items}


def test_a_scale_tidies_its_levels_and_refuses_repeats() -> None:
    scale = StateScale(id="t", name=" Trust ", levels=[" none ", "full"])
    assert scale.name == "Trust" and scale.levels == ["none", "full"]
    assert scale.rank("FULL") == 1 and scale.rank("half") is None
    with pytest.raises(ValidationError):
        StateScale(id="t", name="T", levels=["a", "A"])
    with pytest.raises(ValidationError):
        StateScale(id="t", name="T", levels=["a"])
    with pytest.raises(ValidationError):
        StateScale(id="t", name="T", levels=["a", "b"], ordered=False, one_step=True)
    with pytest.raises(ValidationError):
        ScaleSet(scales=[TRUST, TRUST])
    with pytest.raises(ValidationError):
        ArcStage(name="x", scale_id="trust")
    assert read_scales({"scales": "nonsense"}) == ScaleSet()
    assert read_scales(None) == ScaleSet()


def test_progress_says_toward_or_away_from_where_the_arc_ends() -> None:
    readings, _scenes_in_order, _titles = _readings(
        ArcStage(name="Alone", scale_id="trust", level="None"),
        ArcStage(name="Hope", scene_id="s1", scale_id="trust", level="Some"),
        ArcStage(name="Betrayed", scene_id="s2", scale_id="trust", level="Wary"),
        ArcStage(name="Trusts", scene_id="s4", scale_id="trust", level="Complete"),
    )
    [progress] = scale_progress(readings, ScaleSet(scales=[TRUST]))
    [row] = progress.rows
    assert (row.start, row.target) == ("None", "Complete")
    assert [(s.scene_number, s.level, s.toward) for s in row.steps] == [
        (1, "Some", "toward"),
        (2, "Wary", "away"),
        (4, "Complete", "reached"),
    ]


def test_an_unordered_scale_says_no_direction() -> None:
    sides = StateScale(id="side", name="Side", levels=["Crown", "Rebels"], ordered=False)
    readings, _s, _t = _readings(
        ArcStage(name="Turns", scene_id="s1", scale_id="side", level="Rebels")
    )
    [progress] = scale_progress(readings, ScaleSet(scales=[sides]))
    assert progress.rows[0].steps[0].toward == "reached"
    readings, _s, _t = _readings(
        ArcStage(name="Loyal", scene_id="s1", scale_id="side", level="Crown"),
        ArcStage(name="Turns", scene_id="s2", scale_id="side", level="Rebels"),
    )
    [progress] = scale_progress(readings, ScaleSet(scales=[sides]))
    assert [step.toward for step in progress.rows[0].steps] == [None, "reached"]


def test_gone_scales_and_levels_and_jumps_are_flagged() -> None:
    one_step = TRUST.model_copy(update={"one_step": True})
    readings, _s, titles = _readings(
        ArcStage(name="Wary", scene_id="s1", scale_id="trust", level="Wary"),
        ArcStage(name="All in", scene_id="s3", scale_id="trust", level="Complete"),
        ArcStage(name="Lost", scene_id="s4", scale_id="trust", level="Gone"),
        ArcStage(name="Elsewhere", scale_id="fear", level="High"),
    )
    codes = [f.code for f in scale_findings(readings, ScaleSet(scales=[one_step]), titles)]
    assert codes == ["arc_level_jump", "arc_level_gone", "arc_scale_gone"]
    assert scale_findings(readings, ScaleSet(scales=[TRUST]), titles)[0].code == "arc_level_gone"


def test_a_fact_lapsing_before_the_next_stage_is_said_once() -> None:
    readings, scenes, titles = _readings(
        ArcStage(name="Settled", scene_id="s1", fact_id="f"),
        ArcStage(name="Leaves", scene_id="s4"),
    )
    standing = {"s1": "holds", "s2": "not_then", "s3": "not_then", "s4": "not_then"}
    readings = read_arcs(
        [
            *scenes,
            _arc(
                *[
                    ArcStage(name="Settled", scene_id="s1", fact_id="f"),
                    ArcStage(name="Leaves", scene_id="s4"),
                ]
            ),
        ],
        scenes,
        lambda _fact, scene: standing[scene.id] if scene else "unclear",  # type: ignore[arg-type,return-value]
    )
    [finding] = lapse_findings(
        readings,
        scenes,
        lambda _fact, scene: standing[scene.id] if scene else "unclear",  # type: ignore[arg-type,return-value]
        titles,
    )
    assert finding.code == "arc_fact_lapses"
    assert "“s2” happens" in finding.message and "reached in “s4”" in finding.message


def test_a_fact_that_cannot_be_judged_between_stages_is_not_flagged() -> None:
    _r, scenes, titles = _readings()
    arc = _arc(ArcStage(name="Settled", scene_id="s1", fact_id="f"))
    readings = read_arcs(
        [*scenes, arc], scenes, lambda _f, s: "holds" if s and s.id == "s1" else "unclear"
    )
    assert lapse_findings(readings, scenes, lambda _f, s: "unclear", titles) == []


def test_removing_a_scale_or_a_level_in_use_is_refused_plainly() -> None:
    wary = [ArcStage(name="Wary of all", scale_id="trust", level="Wary")]
    arcs = [("Mara warms", wary)]
    assert "used by “Mara warms”" in (
        scale_removal_problem(ScaleSet(scales=[TRUST]), ScaleSet(), arcs) or ""
    )
    fewer = TRUST.model_copy(update={"levels": ["None", "Some", "Complete"]})
    said = scale_removal_problem(ScaleSet(scales=[TRUST]), ScaleSet(scales=[fewer]), arcs)
    assert said is not None and "level “Wary”" in said
    unused = TRUST.model_copy(update={"levels": ["None", "Wary", "Some"]})
    assert scale_removal_problem(ScaleSet(scales=[TRUST]), ScaleSet(scales=[unused]), arcs) is None
