"""What each check that needs no model reads, as fingerprints (CON-007)."""

import pytest

from backend.domain.check_fingerprints import (
    CHECK_SOURCES,
    CHECKS_VERSION,
    OTHER,
    SCENE_CHECKS,
    brief_inputs,
    changed_checks,
    check_fingerprints,
    check_for,
    checks_version,
    code_words,
    digest_of,
    finding_dependency,
    prose_fingerprint,
    rules_fingerprint,
)
from backend.domain.drafting import DraftBrief, PlannedReveal, SecretToGuard

pytestmark = pytest.mark.unit


def _inputs(brief: DraftBrief, prose: str = "Tobin waited.") -> dict[str, str]:
    return {
        "prose": prose_fingerprint(prose),
        "names": digest_of({"tobin": ["ent_tobin"]}),
        "timeline": digest_of(["main", 4]),
        **brief_inputs(brief),
    }


def test_each_finding_code_belongs_to_one_check_and_unknown_codes_to_the_catch_all():
    assert check_for("stock_phrase").name == "stock"
    assert check_for("continuity_simultaneity").name == "continuity"
    assert check_for("learned_later_said").name == "knowledge"
    assert check_for("something_new") is OTHER
    names = [check.name for check in SCENE_CHECKS]
    assert len(names) == len(set(names))
    assert code_words("continuity_two_places") == "Continuity: two places"


def test_a_change_to_one_slice_changes_only_the_checks_that_read_it():
    brief = DraftBrief(point_of_view="Tobin", point_of_view_names=("Tobin",))
    before = check_fingerprints(_inputs(brief))
    assert changed_checks(before, check_fingerprints(_inputs(brief))) == set()

    brief.secrets.append(SecretToGuard("Mara is in the guild", ("Mara", "the guild")))
    assert changed_checks(before, check_fingerprints(_inputs(brief))) == {"secrets", "other"}

    brief2 = DraftBrief(point_of_view="Tobin", point_of_view_names=("Tobin",))
    brief2.reveals.append(PlannedReveal("Mara lies", ("Mara",)))
    assert changed_checks(before, check_fingerprints(_inputs(brief2))) == {"reveals", "other"}

    words = check_fingerprints(
        _inputs(DraftBrief(point_of_view="Tobin", point_of_view_names=("Tobin",)), "Cold.")
    )
    assert changed_checks(before, words) == {
        "names",
        "secrets",
        "knowledge",
        "reveals",
        "presence",
        "looks",
        "travel",
        "stock",
        "length",
        "other",
    }


def test_the_continuity_findings_are_not_part_of_the_brief_slices():
    from backend.domain.drafting import DraftFinding, Severity

    plain = DraftBrief()
    found = DraftBrief(continuity=[DraftFinding("continuity_x", Severity.NOTE, "x")])
    assert brief_inputs(plain) == brief_inputs(found)


def test_digests_are_stable_and_never_hold_the_words():
    assert prose_fingerprint("Tobin waited.") == prose_fingerprint("Tobin waited.")
    assert prose_fingerprint("Tobin waited.") != prose_fingerprint("Tobin  waited.")
    # The same text in another Unicode form is the same words.
    assert prose_fingerprint("café") == prose_fingerprint("café")
    assert len(prose_fingerprint("x")) == 64
    assert digest_of({"b": 1, "a": {2, 1}}) == digest_of({"a": [1, 2], "b": 1})


def test_a_dependency_follows_what_the_finding_says_and_the_rules_their_versions():
    one = finding_dependency("secret", "s", "This may give away a secret Tobin does not know.")
    assert one == finding_dependency(
        "secret", "s", "This may give away a secret  Tobin does not know."
    )
    assert one != finding_dependency(
        "secret", "s", "This may give away a secret Tobin Vale does not know."
    )

    class Rule:
        def __init__(self, version: int) -> None:
            self.id, self.version, self.status, self.enforcement = "r", version, "active", "warning"

    assert rules_fingerprint([Rule(1)]) == rules_fingerprint([Rule(1)])
    assert rules_fingerprint([Rule(1)]) != rules_fingerprint([Rule(2)])


def test_the_version_of_the_checks_follows_the_code_they_run(monkeypatch):
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2] / "backend"
    sources = [(name, (backend / name).read_bytes()) for name in CHECK_SOURCES]
    # Worked out from the code itself, and the same for the same code in any order.
    assert checks_version(sources) == CHECKS_VERSION
    assert checks_version(reversed(sources)) == CHECKS_VERSION
    assert {"domain/drafting.py", "domain/appearance.py", "domain/continuity.py"} <= set(
        CHECK_SOURCES
    )
    # Any change to the code (a stock phrase added, say) is a new version of the checks.
    changed = [
        (name, raw + b"\n# one more stock phrase\n" if name == "domain/drafting.py" else raw)
        for name, raw in sources
    ]
    assert checks_version(changed) != CHECKS_VERSION
    assert checks_version(changed).startswith("scene-checks/")
    # The version is part of every check's fingerprint, so nothing kept is reused.
    import backend.domain.check_fingerprints as module

    inputs = _inputs(DraftBrief())
    before = check_fingerprints(inputs)
    monkeypatch.setattr(module, "CHECKS_VERSION", checks_version(changed))
    assert changed_checks(before, check_fingerprints(inputs)) == set(before)
