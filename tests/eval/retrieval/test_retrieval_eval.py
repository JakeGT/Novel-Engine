"""What a scene's context gives, against hand labels (CTX-010).

The numbers are pinned in ``baseline.json``. Regenerate after a deliberate change with
``NOVEL_EVAL_PIN=1 uv run pytest -q tests/eval/retrieval``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.eval.report import record
from tests.eval.retrieval.harness import evaluate

pytestmark = [pytest.mark.eval, pytest.mark.contract]

BASELINE = Path(__file__).with_name("baseline.json")


def _pinned(measured: dict) -> dict:
    return {
        **{key: value for key, value in measured.items() if key != "per_scene"},
        "per_scene": [
            {key: value for key, value in item.items() if key != "about"}
            for item in measured["per_scene"]
        ],
    }


def test_the_context_against_the_labels(auth_client, fake_container) -> None:
    measured = evaluate(auth_client, fake_container)
    record("retrieval", measured)
    if os.environ.get("NOVEL_EVAL_PIN"):
        BASELINE.write_text(json.dumps(_pinned(measured), indent=2, sort_keys=True) + "\n")
    assert _pinned(measured) == json.loads(BASELINE.read_text(encoding="utf-8"))

    # What the numbers say, read without the baseline.
    assert measured["leaks"] == 0, "a secret, later knowledge or another line leaked"
    assert measured["silent_omissions"] == 0, "something relevant was dropped without a reason"
    assert measured["branch_correct"] is True
    assert measured["distractors_given"] == 0
    assert measured["mrr"] == 1.0
    assert measured["recall"] == 1.0
    # With the least room allowed, the lowest-ranked items go first, each said, and
    # everything the scene needs stays.
    small = next(item for item in measured["per_scene"] if item["id"] == "crossing-small-budget")
    whole = next(item for item in measured["per_scene"] if item["id"] == "crossing")
    assert small["left_out_for_room"] > 0 and small["given"] < whole["given"]
    assert small["recall"] == 1.0 and small["omitted"] == []
    # Nothing was asked of a model.
    assert fake_container.ai.calls == []
