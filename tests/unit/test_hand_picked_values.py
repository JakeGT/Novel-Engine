"""A value of a kind of fact whose values the author picks by hand is suggested only when
the passage uses that word itself, not the same letters inside another word (EXT-001)."""

import pytest

from backend.domain.canon.registry import CORE_PREDICATES
from backend.services.intake_workflow import _picked_problem

pytestmark = pytest.mark.unit

CHOICES = {"HAS_STATUS": ("free", "ill", "wounded")}


def _problem(value: str, quote: str) -> str | None:
    return _picked_problem("HAS_STATUS", value, quote, CORE_PREDICATES, CHOICES)


def test_the_word_inside_another_word_is_not_the_word() -> None:
    assert _problem("ill", "She will go") == "HAS_STATUS: the passage does not use the word “ill”"
    assert _problem("free", "a freeman stood there") is not None


def test_the_word_itself_in_any_case_or_beside_punctuation_is_used() -> None:
    assert _problem("ill", "She was ill.") is None
    assert _problem("ill", "Ill, and alone") is None
    assert _problem("wounded", "he was Wounded") is None
    assert _problem("free", "“Free!” she cried") is None
