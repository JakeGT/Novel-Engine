"""The picture and words search evaluation against its pinned numbers (CTX-014).

Runs in ``make test`` with the deterministic stand-in: it measures the harness, gates
and ranking, not a model. Regenerate the pin after a deliberate change with
``NOVEL_EVAL_PIN=1 uv run pytest -q tests/eval/multimodal``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from backend.adapters.embedding_fake import DeterministicEmbedding
from tests.embedding_doubles import MediaWorld
from tests.eval.multimodal.harness import evaluate, evaluate_configuration, pinned
from tests.eval.report import record

pytestmark = [pytest.mark.eval, pytest.mark.unit]

BASELINE = Path(__file__).with_name("baseline.json")


def test_the_multimodal_harness_against_its_pin() -> None:
    measured = evaluate()
    record("multimodal", measured)
    if os.environ.get("NOVEL_EVAL_PIN"):
        BASELINE.write_text(json.dumps(pinned(measured), indent=2, sort_keys=True) + "\n")
    assert pinned(measured) == json.loads(BASELINE.read_text(encoding="utf-8"))
    for item in measured["configurations"]:
        # Gates are deterministic: zero leaks is the release gate, not a target.
        assert item["leaks"] == 0, item["leaked_ids"]
        assert item["links_first"] == 1.0
        assert item["rebuild_equivalent"] and item["stopped_build_left_nothing"]


def test_the_leak_count_can_fail(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A gate broken on purpose must show up as leaks, or the zero above means nothing."""
    monkeypatch.setattr(MediaWorld, "eligible", lambda self, *args, **kwargs: True)
    broken = asyncio.run(
        evaluate_configuration("broken-gates", DeterministicEmbedding(dimension=128))
    )
    assert broken["leaks"] > 0


def test_retrieval_numbers_can_fail() -> None:
    """A stand-in told nothing about the pictures cannot find them by words."""

    class Blind(DeterministicEmbedding):
        def describe(self, picture, words):  # type: ignore[no-untyped-def]
            return ""

    blind = asyncio.run(evaluate_configuration("blind", Blind(dimension=128)))
    assert blind["words_to_picture"]["recall_at_5"] < 0.5
    assert blind["leaks"] == 0
