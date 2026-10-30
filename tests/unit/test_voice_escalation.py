"""Which passages a strong voice drift sends to the assistant (CON-018): chosen with no
model from what each note measured, a few of them, and in one pass over the text."""

from __future__ import annotations

import time

import pytest

from backend.domain.voice import (
    MAX_FLAGGED,
    MAX_FLAGGED_WORDS,
    VoiceNote,
    flagged_passages,
    measure,
    strong_drift,
)

pytestmark = pytest.mark.unit

SHORT = "Mara waited. The tide came in. Tobin did not come. She left."
LONG = (
    "Mara waited at the end of the harbour wall while the tide came slowly in over the "
    "stones and the boats knocked together and the gulls went round and round above the "
    "water as if they too were waiting for someone who would never come back to them."
)
ADVERBS = "She quietly and slowly and carefully and gently put the lamp down softly."
TEXT = "\n\n".join([SHORT, LONG, SHORT, ADVERBS, SHORT])


def _note(code: str, subject: str = "", start: int | None = None) -> VoiceNote:
    return VoiceNote(
        code, f"A note about {code}.", start, None if start is None else start + 4, subject
    )


def test_strong_means_a_note_of_someone_else_or_two_different_notes() -> None:
    assert strong_drift([_note("voice_tense")])
    assert strong_drift([_note("voice_adverbs"), _note("voice_sentence_length")])
    assert not strong_drift([_note("voice_adverbs"), _note("voice_adverbs")])
    assert not strong_drift([])


def test_each_note_chooses_the_paragraphs_that_show_it() -> None:
    stats = measure(TEXT)
    longer = flagged_passages(TEXT, [_note("voice_sentence_length", "longer")], stats)
    assert LONG in [TEXT[p.start : p.end] for p in longer] and len(longer) <= 2
    adverbs = flagged_passages(TEXT, [_note("voice_adverbs")], stats)
    assert ADVERBS in [TEXT[p.start : p.end] for p in adverbs]
    assert SHORT not in [TEXT[p.start : p.end] for p in adverbs]
    at = TEXT.index("Tobin")
    pointed = flagged_passages(TEXT, [_note("voice_tense_slip", start=at)], stats)
    assert [(p.start, p.why) for p in pointed] == [(0, "A note about voice_tense_slip.")]
    # Passages come in the order of the scene, each once.
    both = flagged_passages(
        TEXT, [_note("voice_adverbs"), _note("voice_sentence_length", "longer")], stats
    )
    assert [p.start for p in both] == sorted({p.start for p in both})


def test_there_are_never_more_than_a_few_passages_or_words() -> None:
    text = "\n\n".join([LONG] * 60)
    notes = [_note(code) for code in ("voice_everyday_words", "voice_adverbs", "voice_leans_on")]
    notes += [_note("voice_sentence_length", "longer"), _note("voice_dialogue", "more")]
    found = flagged_passages(text, notes, measure(text))
    assert 0 < len(found) <= MAX_FLAGGED
    assert sum(len(text[p.start : p.end].split()) for p in found) <= MAX_FLAGGED_WORDS


def test_a_long_paragraph_is_cut_at_a_sentence_end() -> None:
    text = LONG + " " + " ".join([SHORT] * 60)
    (found,) = flagged_passages(text, [_note("voice_everyday_words")], measure(text))
    assert found.end - found.start <= 1_500 and text[found.end - 1] == "."


@pytest.mark.parametrize(
    "text",
    [
        (LONG + "\n\n") * 1_900,
        "“" * 500_000,
        "." * 500_000,
        "\n" * 500_000,
        ("a’" * 250_000),
    ],
    ids=["prose", "open-quotes", "dots", "blank-lines", "apostrophes"],
)
def test_a_500k_character_scene_is_read_in_one_pass(text: str) -> None:
    notes = [
        _note("voice_sentence_length", "shorter"),
        _note("voice_adverbs"),
        _note("voice_tense"),
        _note("voice_speaker_length", "Tobin Ashe\x1elonger"),
        _note("voice_everyday_words"),
    ]
    stats = measure(text)
    started = time.perf_counter()
    flagged_passages(text, notes, stats)
    assert time.perf_counter() - started < 1.5


def test_a_long_paragraph_is_cut_around_what_was_flagged_in_it() -> None:
    """A slip pointed at, or the -ly words, far into a long paragraph are in the passage,
    which still starts at a sentence's start and ends at a sentence's end."""
    plain = " ".join([SHORT] * 60)  # about 3,700 characters of plain sentences
    slip = "Then Tobin turns and runs."
    text = plain + " " + slip + " " + plain
    at = text.index(slip)
    (pointed,) = flagged_passages(text, [_note("voice_tense_slip", start=at)], measure(text))
    passage = text[pointed.start : pointed.end]
    assert slip in passage and len(passage) <= 1_500
    assert passage.startswith(("Mara", "The", "Tobin", "She", "Then")) and passage.endswith(".")

    text = plain + " " + ADVERBS + " " + plain
    (found,) = flagged_passages(text, [_note("voice_adverbs")], measure(text))
    assert ADVERBS in text[found.start : found.end] and found.end - found.start <= 1_500


def test_a_500k_character_paragraph_flagged_near_its_end_is_cut_quickly() -> None:
    filler = "She waits by the water and nothing happens. "
    # One paragraph of 500,000 characters; the slip comes inside what is read (400,000).
    text = filler * 8_600 + "Then Tobin runs. " + filler * 2_800
    assert len(text) > 500_000
    at = text.index("Then Tobin runs.")
    started = time.perf_counter()
    (found,) = flagged_passages(text, [_note("voice_tense_slip", start=at)], measure(text))
    assert time.perf_counter() - started < 1.5
    passage = text[found.start : found.end]
    assert "Then Tobin runs." in passage and len(passage) <= 1_500
