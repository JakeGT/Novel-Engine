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
    narrated = "\u201cGo,\u201d Mara said, and I stood at the harbor."
    assert "viewpoint_missing" not in _codes(check_draft(narrated, BRIEF, []))


def test_an_i_said_aloud_is_not_first_person_narration() -> None:
    for text in (
        '"I will not sail tonight," said Mara at the harbor.',
        "\u201cI will not sail tonight,\u201d said Mara at the harbor.",
        "\u2018I can\u2019t sail, not with my hands,\u2019 said Mara at the harbor.",
    ):
        assert "viewpoint_missing" in _codes(check_draft(text, BRIEF, [])), text


def test_a_secret_named_only_to_deny_it_is_a_note_not_a_warning() -> None:
    for text in (
        "Mara had never so much as spoken to the Harbor Guild.",
        "Mara wasn\u2019t one of the Harbor Guild, Tobin was sure.",
        "Nothing tied Mara to the Harbor Guild. Not Mara, he thought.",
    ):
        (finding,) = [f for f in check_draft(text, BRIEF, []) if f.code.startswith("secret")]
        assert finding.code == "secret_denied" and finding.severity is Severity.NOTE, text
        assert finding.message.startswith("This names both sides of a secret Tobin does not know")
        assert "even to deny it: Mara is a member of the Harbor Guild." in finding.message
    # A denial beside a sentence that gives it away is still a warning.
    both = "Mara was no friend of the Harbor Guild. Mara wore the Harbor Guild token."
    assert "secret" in _codes(check_draft(both, BRIEF, []))
    assert "secret_denied" not in _codes(check_draft(both, BRIEF, []))
    # A "not" after both are named is about something else.
    after = "Mara pinned the Harbor Guild token inside her coat and thought he had not seen."
    assert "secret" in _codes(check_draft(after, BRIEF, []))
    # A "no" in another sentence is not a denial of this one.
    apart = "No bell rang. Mara slipped her Harbor Guild token into her coat."
    assert "secret" in _codes(check_draft(apart, BRIEF, []))
    # "Nobody" or "known" are not a not.
    plain = "Nobody saw Mara take the Harbor Guild oath; it was known."
    assert "secret" in _codes(check_draft(plain, BRIEF, []))


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


def test_what_drafting_would_give_is_one_note_and_skips_facts_already_flagged() -> None:
    from backend.domain.drafting import LaterKnowledge

    def later(sentence: str, **flags) -> LaterKnowledge:
        return LaterKnowledge(
            who="Tobin",
            where="in “The Market”",
            sentence=sentence,
            names=("Mara", "the Gull"),
            **flags,
        )

    brief = DraftBrief(
        point_of_view="Tobin",
        learned_later=[
            later("Mara owns the Gull", viewpoint=True, in_context=True),
            later("Mara is at the harbor", viewpoint=False, in_context=True),
            later("Mara is a guild member", viewpoint=False, in_context=True),
        ],
    )
    findings = check_draft("Tobin saw that Mara and the Gull were one.", brief, [])
    context = [f for f in findings if f.code == "learned_later_context"]
    assert len(context) == 1
    assert "Mara is at the harbor" in context[0].message
    assert "Mara is a guild member" in context[0].message
    assert "Mara owns the Gull" not in context[0].message
