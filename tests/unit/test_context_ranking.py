"""The ranking signals of a scene's context (CTX-007): each one moves a score by an
amount it records, and says why in plain words."""

import pytest

from backend.domain.context import (
    DISTANCE_FLOOR,
    LATER_WEIGHT,
    ContextItem,
    ContextSection,
    distance_signal,
    distance_weight,
    diversify,
    importance_signal,
    likeness,
    shingles,
    with_signals,
)

pytestmark = pytest.mark.unit


def _passage(key: str, text: str, score: float) -> ContextItem:
    return ContextItem(
        key=key,
        section=ContextSection.WRITING,
        text=text,
        reason="It mentions Mara.",
        authority="writing",
        record_type="source_version",
        record_id=f"ver_{key}",
        score=score,
    )


def test_distance_halves_every_four_scenes_and_never_falls_below_the_floor():
    assert distance_weight(0) == distance_weight(1) == 1.0
    assert distance_weight(5) == pytest.approx(0.5)
    assert distance_weight(100) == DISTANCE_FLOOR
    assert distance_weight(-1) == LATER_WEIGHT
    assert distance_signal(4.0, 1, "A") is None
    later = distance_signal(4.0, -2, "Z: the end")
    assert later is not None and later.effect == pytest.approx(-2.0)
    assert later.note == "It is from “Z: the end”, which comes later in the story than this scene."
    back = distance_signal(4.0, 5, "")
    assert back is not None and back.note == "It is five scenes back."


def test_importance_moves_a_score_by_the_mark_and_says_whose():
    main = importance_signal(2.0, "main", "Mara")
    assert main is not None and main.effect == pytest.approx(1.0)
    assert main.note == "You marked Mara as one of your main entries."
    background = importance_signal(2.0, "background", "the quay")
    assert background is not None and background.effect < 0
    assert importance_signal(2.0, None, "Mara") is None
    assert importance_signal(2.0, "nonsense", "Mara") is None
    assert with_signals(2.0, [main, background]) == pytest.approx(2.0 + 1.0 - 0.6)


def test_a_near_copy_ranks_lower_and_says_like_which_and_others_keep_their_score():
    first = _passage("p1", "From “Dawn”: Mara tied up her boat and did not look at him.", 3.0)
    copy = _passage("p2", "From “Noon”: Mara tied up her boat and did not look at him again.", 2.9)
    other = _passage("p3", "From “Dusk”: The guild bell rang twice over the empty water.", 2.0)
    ranked = diversify([copy, other, first])
    assert [item.key for item in ranked] == ["p1", "p3", "p2"]
    by_key = {item.key: item for item in ranked}
    assert by_key["p1"].score == 3.0 and by_key["p1"].signals == ()
    assert by_key["p3"].score == 2.0 and by_key["p3"].signals == ()
    (signal,) = by_key["p2"].signals
    assert signal.name == "diversity" and signal.effect < 0
    assert by_key["p2"].score == pytest.approx(2.9 + signal.effect)
    assert "from “Dawn”" in signal.note and signal.note in by_key["p2"].reason


def test_diversity_is_deterministic_and_ignores_case_and_punctuation():
    a = shingles("Mara tied up her boat.")
    b = shingles("mara TIED up her boat")
    assert likeness(a, b) == 1.0
    assert likeness(a, frozenset()) == 0.0
    items = [_passage(f"p{i}", "the same words every time here", 1.0) for i in range(3)]
    assert [item.key for item in diversify(items)] == [item.key for item in diversify(items[::-1])]
