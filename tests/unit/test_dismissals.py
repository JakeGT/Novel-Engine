"""Where a dismissed finding points, and when it comes back (CON-016)."""

from datetime import UTC, datetime, timedelta

import pytest

from backend.domain.dismissals import (
    DismissReason,
    FindingDismissal,
    FindingKind,
    anchor,
    expiry,
    fingerprint,
)
from backend.domain.drafting import DraftBrief, check_draft

pytestmark = pytest.mark.unit

TEXT = "Tobin waited. The air was palpable with salt. Mara did not come.\n\nGulls cried."


def _at(word: str) -> tuple[int, int]:
    start = TEXT.index(word)
    return start, start + len(word)


def test_a_finding_is_anchored_to_the_sentence_it_sits_in() -> None:
    assert anchor(TEXT, *_at("palpable")) == "The air was palpable with salt."
    assert anchor(TEXT, *_at("Tobin")) == "Tobin waited."
    assert anchor(TEXT, *_at("Gulls")) == "Gulls cried."
    # A passage that runs over a sentence's end is its own anchor.
    start = TEXT.index("salt")
    assert anchor(TEXT, start, TEXT.index("come") + 4) == "salt. Mara did not come"
    # About the scene as a whole, or out of range: no anchor.
    assert anchor(TEXT, None, None) == ""
    assert anchor(TEXT, 5, 500) == ""


def test_editing_another_sentence_keeps_the_fingerprint_and_editing_its_own_changes_it() -> None:
    def key(text: str) -> str:
        start = text.index("palpable")
        return fingerprint(
            FindingKind.CHECK, "stock_phrase", "palpable", anchor(text, start, start + 8)
        )

    before = key(TEXT)
    assert key(TEXT.replace("Mara did not come.", "Mara came at last.")) == before
    # Spacing alone is not a change of words.
    assert key(TEXT.replace("The air was", "The  air was")) == before
    assert key(TEXT.replace("with salt", "with brine")) != before
    # Another kind of finding on the same words is another finding.
    start = TEXT.index("palpable")
    other = fingerprint(FindingKind.NOTE, "voice", "", anchor(TEXT, start, start + 8))
    assert other != before
    assert len(before) == 64 and "palpable" not in before


def test_every_check_says_what_it_is_about() -> None:
    brief = DraftBrief(point_of_view="Tobin", point_of_view_names=("Tobin",), words_asked=900)
    found = check_draft("She felt a sense of dread at the quay.", brief, [])
    subjects = {(f.code, f.subject) for f in found}
    assert ("stock_phrase", "a sense of") in subjects
    assert ("viewpoint_missing", "Tobin") in subjects
    assert ("short", "900") in subjects


def test_a_dismissal_lapses_when_it_runs_out_or_is_withdrawn() -> None:
    now = datetime(2026, 10, 17, tzinfo=UTC)
    dismissal = FindingDismissal(
        workspace_id="w",
        project_id="p",
        branch_id="b",
        id="d",
        plan_id="s",
        fingerprint="0" * 64,
        kind=FindingKind.CHECK,
        code="stock_phrase",
        reason=DismissReason.INTENDED,
        note="",
        expires_at=expiry(now, 7),
        created_by=None,
        created_at=now,
    )
    assert dismissal.applies(now + timedelta(days=6))
    assert not dismissal.applies(now + timedelta(days=7))
    forever = FindingDismissal(**{**dismissal.__dict__, "expires_at": None})
    assert forever.applies(now + timedelta(days=3650))
    withdrawn = FindingDismissal(**{**forever.__dict__, "withdrawn_at": now})
    assert not withdrawn.applies(now)
    with pytest.raises(ValueError):
        expiry(now, 3)
