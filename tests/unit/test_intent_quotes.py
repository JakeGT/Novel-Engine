"""The scene-intent prompt asks for quotes long enough to be kept, and the browser tests'
stand-in model gives them (STY-003)."""

from __future__ import annotations

import pytest

from backend.services.planning_help import (
    MIN_QUOTE_WORDS,
    _shown,
    scene_intent_messages,
)
from scripts.e2e.fake_model import _intent

pytestmark = pytest.mark.unit


def test_the_prompt_asks_for_quotes_of_at_least_the_kept_length() -> None:
    system = scene_intent_messages("What happens: Mara runs.", "")[0]["content"]
    assert f"at least {MIN_QUOTE_WORDS} words in a row" in system


@pytest.mark.parametrize(
    "happens",
    [
        "Mara tries to steal the ledger before the tide turns.",
        # A short part quotes the whole line rather than a word or two.
        "Mara runs but fails.",
    ],
)
def test_the_stand_in_models_quotes_are_long_enough_to_be_kept(happens: str) -> None:
    card = f"Scene: The theft\nWhat happens: {happens}"
    messages = scene_intent_messages(card, "")
    answer = _intent({"messages": messages})
    assert answer["goal"]
    for part in ("goal", "obstacle"):
        shown, _ = _shown(answer[f"{part}_words"], card, "")
        assert shown, (part, answer)
