"""Name-finding is measured against a labelled passage, and the numbers are pinned."""

import json
from pathlib import Path

import pytest

from backend.devtools.intake_eval import evaluate_names, parse_labelled, score_facts

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "intake_eval"


def test_labels_are_stripped_and_located() -> None:
    text, gold = parse_labelled(["{{mara:name|Mara Voss}} met ", "{{ivo:mark|@Ivo}}."])
    assert text == "Mara Voss met @Ivo."
    assert [(text[s.start : s.end], s.key, s.kind) for s in gold] == [
        ("Mara Voss", "mara", "name"),
        ("@Ivo", "ivo", "mark"),
    ]


def test_name_finding_matches_the_recorded_baseline() -> None:
    """A change to how names are found must move these numbers on purpose.

    Regenerate with:
    python -m backend.devtools.intake_eval tests/fixtures/intake_eval/lantern_coast.json
    """
    case = json.loads((FIXTURES / "lantern_coast.json").read_text(encoding="utf-8"))
    baseline = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
    measured = evaluate_names(case)
    assert measured == baseline
    # What the numbers say, so a reader need not open the file.
    assert measured["precision"] >= 0.9
    assert measured["by_kind"]["name"]["recall"] == 1.0
    assert measured["by_kind"]["alias"]["recall"] == 1.0
    assert measured["by_kind"]["answered"]["recall"] == 1.0
    # Never found without a model or an answer, and reported as such rather than hidden.
    for kind in ("partial", "epithet", "pronoun"):
        assert measured["by_kind"][kind]["recall"] == 0.0
    # A common word that is also someone's name is still mistaken at the start of a sentence.
    assert measured["wrongly_found"] == ["Hope"]


def test_drafted_facts_are_scored_against_expected_ones() -> None:
    expected = [
        ("Mara Voss", "LOCATED_AT", "Glasslight Tower"),
        ("Ivo Renn", "KNOWS", "Mara Voss"),
    ]
    drafted = [
        ("mara  voss", "LOCATED_AT", "Glasslight Tower"),
        ("Ivo Renn", "OWNS", "Tideglass Lens"),
        ("Ivo Renn", "OWNS", "Tideglass Lens"),
    ]
    assert score_facts(drafted, expected) == {
        "drafted": 2,
        "expected": 2,
        "correct": 1,
        "precision": 0.5,
        "recall": 0.5,
    }
    assert score_facts([], [])["precision"] == 1.0
