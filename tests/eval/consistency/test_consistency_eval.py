"""The deterministic checks against labelled defects and near misses (CON-012).

The numbers are pinned: a change to a check or a case must move them on purpose
(regenerate with ``uv run python -m tests.eval.consistency.harness``). Every case a
check gets wrong must say why, as a known limit or a suspected bug.
"""

from __future__ import annotations

import json

import pytest

from tests.eval.consistency.cases import all_cases
from tests.eval.consistency.harness import BASELINE, evaluate, pinned, run_case
from tests.eval.report import record

pytestmark = [pytest.mark.eval, pytest.mark.unit]


@pytest.fixture(scope="module")
def measured() -> dict:
    found = evaluate()
    record("consistency", found)
    return found


def test_the_case_ids_are_unique_and_every_family_has_defects_and_near_misses() -> None:
    cases = all_cases()
    assert len({case.id for case in cases}) == len(cases)
    for family in {case.family for case in cases}:
        mine = [case for case in cases if case.family == family]
        assert any(case.defect for case in mine), family
        assert any(not case.defect for case in mine), family
        assert all(case.codes for case in mine)


def test_the_measured_numbers_match_the_pinned_baseline(measured: dict) -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert pinned(measured) == baseline


def test_every_disagreement_is_a_stated_limit_or_a_suspected_bug(measured: dict) -> None:
    assert measured["unexplained"] == []
    stated = set(measured["known_limits"]) | set(measured["suspected_bugs"])
    # A case marked as a limit that the check now gets right should lose its mark.
    assert set(measured["disagreements"]) == stated


def test_what_the_numbers_say(measured: dict) -> None:
    """Read without opening the baseline."""
    overall = measured["overall"]
    assert overall["recall"] >= 0.85 and overall["precision"] >= 0.9
    for family in ("rules", "knowledge", "plan"):
        assert measured["families"][family]["recall"] == 1.0
    for family in ("continuity", "knowledge", "plan"):
        assert measured["families"][family]["false_positive"] == 0
    # "64 years" is read as 64; a denial of a secret is a note, not a warning.
    assert measured["false_positives"] == []
    # A secret given away by a pronoun is never caught without a model.
    assert "drafting/secret-by-pronoun" in measured["missed"]
    assert measured["cost_tokens"] == 0


def test_the_checks_stay_fast_on_a_larger_story(measured: dict) -> None:
    """Generous ceilings: timings vary by machine, so they are reported, not pinned."""
    scale = measured["scale"]
    assert scale["continuity"]["ms"] < 5_000
    assert scale["rules"]["ms"] < 5_000
    assert scale["drafting"]["ms"] < 5_000


@pytest.mark.parametrize(
    "case",
    [case for case in all_cases() if case.suspected_bug],
    ids=lambda case: case.id,
)
def test_suspected_bugs_in_the_checks(case) -> None:
    """Each one is written as the check should behave; it fails until the check is fixed."""
    result = run_case(case)
    if result["agrees"]:
        pytest.fail(f"{case.id} now agrees with its label; drop its suspected_bug mark")
    pytest.xfail(case.suspected_bug or "")
