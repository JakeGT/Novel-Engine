"""Checks on a scene's prose that need no model (ADR-022)."""

from __future__ import annotations

from backend.domain.drafting import (
    DraftBrief,
    PlannedReveal,
    SecretToGuard,
    Severity,
    check_draft,
)
from backend.domain.sources import Mention, MentionStatus

BRIEF = DraftBrief(
    point_of_view="Tobin",
    point_of_view_names=("Tobin", "Tobin Ashe"),
    place="Lantern Harbor",
    place_names=("Lantern Harbor", "the harbor"),
    secrets=[SecretToGuard("Mara is a member of the Harbor Guild", ("Mara", "Harbor Guild"))],
    reveals=[PlannedReveal("Mara owns the Gull", ("Mara", "the Gull"))],
    words_asked=40,
)


def _codes(findings) -> list[str]:
    return [finding.code for finding in findings]


def test_a_clean_scene_gets_no_findings() -> None:
    text = (
        "Tobin waited at Lantern Harbor while the bells rang. "
        "Mara tied up the Gull and did not look at him. " * 3
    )
    assert check_draft(text, BRIEF, []) == []


def test_a_secret_in_one_paragraph_is_a_warning_and_points_at_the_paragraph() -> None:
    text = (
        "Tobin waited at Lantern Harbor.\n\n"
        "Mara slipped her Harbor Guild token into her coat. Mara owns the Gull, he thought."
    )
    findings = check_draft(text, BRIEF, [])
    secret = findings[0]
    assert secret.code == "secret" and secret.severity is Severity.WARNING
    assert text[secret.start : secret.end].startswith("Mara slipped")


def test_names_in_different_paragraphs_are_not_a_secret_given_away() -> None:
    text = "Tobin watched Mara at Lantern Harbor and the Gull.\n\nThe Harbor Guild bell rang."
    assert "secret" not in _codes(check_draft(text, BRIEF, []))


def test_missing_reveal_place_viewpoint_and_length_are_notes() -> None:
    findings = check_draft("Rain fell.", BRIEF, [])
    assert set(_codes(findings)) == {
        "reveal_missing",
        "place_missing",
        "viewpoint_missing",
        "short",
    }
    assert all(f.severity is Severity.NOTE for f in findings)


def test_first_person_does_not_count_as_a_missing_viewpoint() -> None:
    assert "viewpoint_missing" not in _codes(check_draft("I stood at the harbor.", BRIEF, []))


def test_stock_phrasing_is_noted_where_it_occurs() -> None:
    text = "Tobin felt a sense of dread at Lantern Harbor. It was a testament to Mara and the Gull."
    stock = [f for f in check_draft(text, BRIEF, []) if f.code == "stock_phrase"]
    assert [text[f.start : f.end] for f in stock] == ["a sense of", "testament to"]


def test_a_marked_new_name_is_a_question_once() -> None:
    text = "Tobin met @Brannoc at Lantern Harbor. @Brannoc smiled at Mara and the Gull."
    mentions = [
        Mention(10, 18, "Brannoc", True, MentionStatus.UNKNOWN),
        Mention(40, 48, "Brannoc", True, MentionStatus.UNKNOWN),
    ]
    new = [f for f in check_draft(text, BRIEF, mentions) if f.code == "new_name"]
    assert len(new) == 1 and "“Brannoc” is a new name" in new[0].message
