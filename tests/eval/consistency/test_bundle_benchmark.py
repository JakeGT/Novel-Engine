"""The deterministic bundle prototype against full-context and vector-only bundling (CON-013).

Pinned, since nothing in it varies: regenerate with
``uv run python -m tests.eval.consistency.bundles`` after a deliberate change.
"""

from __future__ import annotations

import json

import pytest

from tests.eval.consistency.bundles import BASELINE, BUDGETS, evaluate, pinned
from tests.eval.report import record

pytestmark = [pytest.mark.eval, pytest.mark.unit]


@pytest.fixture(scope="module")
def measured() -> dict:
    found = evaluate()
    record("bundles", found)
    return found


def test_the_benchmark_matches_the_pinned_baseline(measured: dict) -> None:
    assert pinned(measured) == json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_same_story_always_gives_the_same_groups() -> None:
    assert pinned(evaluate()) == pinned(evaluate())


def test_what_the_comparison_shows(measured: dict) -> None:
    """Read without opening the baseline: what each strategy covers and costs."""
    for name in BUDGETS:
        result = measured["budgets"][name]
        ours, full, nearest = (
            result["deterministic"],
            result["full_context"],
            result["vector_only"],
        )
        # The deterministic groups cover the same defects at every budget...
        assert ours["covered"] == measured["budgets"]["roomy"]["deterministic"]["covered"]
        # ...more than the nearest records do, and every check of every passage is planned.
        assert ours["defects_covered"] > nearest["defects_covered"]
        assert ours["coverage_complete"] and ours["every_passage_read"]
        # Passages read by several checks cost more than reading the story once.
        assert ours["content_tokens"] > full["content_tokens"]
        if full["groups"] > 1:
            # Once the whole story no longer fits, a split separates passages that
            # only disagree with each other.
            assert ours["defects_covered"] > full["defects_covered"]
            assert "sextant-cracked-then-flawless" in full["omitted"]
    # When it fits whole, full context sees every defect (and every secret, at once).
    assert measured["budgets"]["roomy"]["full_context"]["defects_covered"] == 8
    # The one the deterministic plan misses: a paragraph that names no one, in a scene
    # with no viewpoint, read on its own without the record about who "he" is.
    assert measured["budgets"]["roomy"]["deterministic"]["omitted"] == {
        "unnamed-keeper-at-the-light": ["fact:osric_at_fen"]
    }


def test_without_the_story_graph_the_plan_says_it_is_incomplete(measured: dict) -> None:
    fallback = measured["fallback_without_graph"]
    assert fallback["coverage_complete"] is False
    assert fallback["every_passage_read"] is True
    assert fallback["defects_covered"] == 0
