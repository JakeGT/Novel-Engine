"""The story's time as a second clock, and what a policy does with later material
(CTX-004, CTX-007)."""

from dataclasses import replace

import pytest

from backend.domain.context import (
    SCENE_DRAFT_POLICY,
    STORY_TIME_FLOOR,
    ContextItem,
    ContextSection,
    LaterInTime,
    LeftOut,
    assemble,
    basis_digest,
    later_in_time_signal,
    story_time_signal,
    story_time_weight,
)

pytestmark = pytest.mark.unit


def test_story_time_halves_every_half_life_and_never_falls_below_its_floor() -> None:
    assert story_time_weight(0, 2.0) == 1.0
    assert story_time_weight(2.0, 2.0) == pytest.approx(0.5)
    assert story_time_weight(1.0, 2.0) == pytest.approx(0.5**0.5)
    assert story_time_weight(100.0, 2.0) == STORY_TIME_FLOOR
    # With no spread of times to measure against, or an open-ended gap, nothing moves.
    assert story_time_weight(5.0, None) == 1.0
    assert story_time_weight(float("inf"), 2.0) == 1.0


def test_the_signal_says_how_long_before_in_the_storys_unit() -> None:
    signal = story_time_signal(4.0, 1.0, 4.0, "day")
    assert signal is not None and signal.name == "story time"
    assert signal.note == "In the story's time it happens 1 day before this scene."
    assert signal.effect == pytest.approx(4.0 * (0.5**0.25 - 1.0), abs=1e-3)
    assert story_time_signal(4.0, 2.5, 4.0, "year").note.endswith("2.5 years before this scene.")  # type: ignore[union-attr]
    assert story_time_signal(4.0, 0.0, 4.0, "year") is None


def test_later_material_is_lowered_more_when_certain() -> None:
    certain = later_in_time_signal(4.0, certain=True)
    maybe = later_in_time_signal(4.0, certain=False)
    assert certain.effect < maybe.effect < 0
    assert maybe.note == "It may happen later in the story's time than this scene."


def _item(key: str, *, later: bool = False, hard: bool = False) -> ContextItem:
    return ContextItem(
        key=key,
        section=ContextSection.WRITING,
        text=f"Words of {key}.",
        reason="It is from your writing.",
        authority="writing",
        record_type="source_version",
        record_id=key,
        score=3.0,
        later=later,
        hard=hard,
    )


def test_the_policy_leaves_later_material_out_unless_pinned_or_lowering() -> None:
    items = [_item("now"), _item("then", later=True)]
    left = assemble(items)
    assert [(o.item.key, o.reason) for o in left.left_out] == [("then", LeftOut.LATER)]
    assert [item.key for item in assemble(items, pins=["then"]).items] == ["now", "then"]
    lowering = replace(SCENE_DRAFT_POLICY, later_in_story_time=LaterInTime.LOWER)
    assert {item.key for item in assemble(items, policy=lowering).items} == {"now", "then"}


def test_a_part_summarys_basis_fingerprint_follows_every_scene_summary() -> None:
    one = basis_digest([("scn_1", "sum_1"), ("scn_2", "sum_2")])
    assert one == basis_digest([("scn_1", "sum_1"), ("scn_2", "sum_2")])
    assert one != basis_digest([("scn_1", "sum_9"), ("scn_2", "sum_2")])
    assert one != basis_digest([("scn_1", "sum_1")])
