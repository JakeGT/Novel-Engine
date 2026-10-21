"""The author's voice in numbers, worked out without a model (CON-018)."""

import time

import pytest

from backend.domain.drift import PairFact, RangeScene, relationship_drift
from backend.domain.voice import (
    MIN_SCENE_WORDS,
    baseline,
    dialogue_mask,
    drift,
    measure,
    trends,
)

pytestmark = pytest.mark.unit

THIRD_PAST = (
    "Mara walked down to the harbor before the light came up over the water. "
    "She looked at the boats and counted the ones that had come back in the night. "
    "Tobin was waiting by the wall with his coat buttoned against the wind. "
    '"You are late," he said, and she shrugged and sat down beside him. '
    "They watched the gulls fight over a crust somebody had dropped on the stones. "
    "He had brought bread and she took half of it without asking for it. "
)

FIRST_PRESENT = (
    "I walk down to the harbor before the light comes up, and I look at the boats, "
    "and I count the ones that come back, because counting is the only thing I can do "
    "while the water keeps moving and the gulls keep screaming over the stones. "
    "Tobin waits by the wall, and I sit beside him, and I take the bread he brings. "
)


def _scene(paragraph: str, words: int = 400) -> str:
    text = ""
    while len(text.split()) < words:
        text += paragraph
    return text


def test_a_scene_is_measured_from_its_words_alone():
    stats = measure(_scene(THIRD_PAST))
    assert stats.words >= 400 and stats.english
    assert 10 <= stats.sentence_mean <= 16
    assert sum(stats.bands) == stats.sentences
    assert 0.0 < stats.dialogue_share < 0.15
    assert stats.person == "third"
    assert stats.tense == "past" and (stats.past_share or 0) > 0.85
    assert stats.adverb_rate is not None


def test_dialogue_is_found_in_curly_straight_and_single_quotes_but_not_apostrophes():
    text = '“Go,” she said. "Now," he said. Mara’s coat was wet.'
    mask = dialogue_mask(text)
    assert mask[text.index("Go")] and mask[text.index("Now")]
    assert not mask[text.index("coat")]
    british = "‘Go home,’ she said. It was Mara’s boat."
    mask = dialogue_mask(british)
    assert mask[british.index("home")] and not mask[british.index("boat")]


def test_marks_count_as_the_plain_words_and_offsets_are_kept():
    plain = measure('Mara met Tobin at the wall. "Late," he said.')
    marked = measure('@"Mara" met [[Tobin]] at the wall. "Late," he said.')
    assert marked.words == plain.words
    assert marked.dialogue_share == plain.dialogue_share


def test_adverbs_skip_words_that_only_look_like_them():
    stats = measure(
        "She walked quickly and quietly. Her family only lived nearby. It was a lovely, "
        "friendly, early morning and she was likely to be late."
    )
    assert {word for word, _ in stats.adverbs} == {"quickly", "quietly"}


def test_a_word_used_again_and_again_close_together_is_an_echo_and_names_are_not():
    text = (
        "The gravel crunched under Mara. Gravel in her boots, gravel on the path, more "
        "gravel by the gate. Mara sighed. Mara walked on. Mara waited."
    )
    stats = measure(text)
    assert stats.echoes and stats.echoes[0].word == "gravel" and stats.echoes[0].times == 4
    assert text[stats.echoes[0].start : stats.echoes[0].end].lower().startswith("gravel")
    assert all(echo.word != "mara" for echo in stats.echoes)


def test_i_inside_dialogue_does_not_make_a_scene_first_person():
    text = _scene(
        'Mara walked to the wall. "I will not wait," she said. "I am tired." He nodded. '
        "She sat down and he looked at her for a long time. "
    )
    assert measure(text).person == "third"
    assert measure(_scene(FIRST_PRESENT)).person == "first"


def test_present_tense_and_a_slip_in_past_narration_are_found():
    assert measure(_scene(FIRST_PRESENT)).tense == "present"
    slipped = (
        _scene(THIRD_PAST, 300) + "She walks to the door and opens it. " + _scene(THIRD_PAST, 300)
    )
    stats = measure(slipped)
    assert stats.tense == "past"
    assert stats.slips
    assert "walks" in slipped[stats.slips[0].start : stats.slips[0].end]


def test_text_that_is_not_english_skips_the_english_measures():
    text = _scene(
        "La marea subía despacio sobre las piedras del puerto. Mara esperaba junto al muro. "
    )
    stats = measure(text)
    assert not stats.english
    assert stats.adverb_rate is None and stats.person == "unclear" and stats.tense == "unclear"
    assert stats.sentence_mean > 0


def test_the_usual_needs_enough_scenes_and_words():
    few = baseline([measure(_scene(THIRD_PAST)) for _ in range(2)])
    assert not few.enough
    short = baseline([measure(THIRD_PAST) for _ in range(5)])
    assert not short.enough  # every scene is under the length that is measured
    enough = baseline([measure(_scene(THIRD_PAST, 800)) for _ in range(3)])
    assert enough.enough and enough.person == "third" and enough.tense == "past"


def test_a_scene_that_departs_from_the_usual_gets_notes_and_one_that_does_not_gets_none():
    usual = baseline([measure(_scene(THIRD_PAST, 600)) for _ in range(4)])
    assert drift(measure(_scene(THIRD_PAST, 500)), usual) == []
    notes = {note.code for note in drift(measure(_scene(FIRST_PRESENT, 500)), usual)}
    assert {"voice_person", "voice_tense", "voice_sentence_length"} <= notes
    talky = _scene(
        '"Where were you?" she said. "Out," he said. "Out where?" "Just out, by the water." ',
        500,
    )
    assert "voice_dialogue" in {note.code for note in drift(measure(talky), usual)}


def test_nothing_is_said_about_a_scene_too_short_to_measure():
    usual = baseline([measure(_scene(THIRD_PAST, 600)) for _ in range(4)])
    short = FIRST_PRESENT
    assert len(short.split()) < MIN_SCENE_WORDS
    assert drift(measure(short), usual) == []


def test_a_word_the_scene_leans_on_is_noted():
    usual = baseline([measure(_scene(THIRD_PAST, 600)) for _ in range(4)])
    leaning = _scene(
        THIRD_PAST.replace("looked at", "glanced at").replace("watched", "glanced at"), 500
    )
    notes = [note for note in drift(measure(leaning), usual) if note.code == "voice_leans_on"]
    assert notes and "“glanced”" in notes[0].message


POOL = [
    "Mara walked down to the harbor before the light came up over the water.",
    "She looked at the boats and counted the ones that had come back in the night.",
    "Tobin was waiting by the wall with his coat buttoned against the wind.",
    "They watched the gulls fight over a crust somebody had dropped on the stones.",
    "He had brought bread and she took half of it without asking.",
    "The tide was turning, and the smell of it reached them before the sound did.",
    "It was cold, but neither of them wanted to be the first to say so.",
    "When the bell rang in the town, she stood and brushed the sand from her knees.",
    "He did not ask where she was going, and she did not tell him.",
    "There was a light in the guild hall, which meant somebody was still awake.",
]


def _varied(seed: int, words: int) -> str:
    import random

    rng = random.Random(seed)
    text = ""
    while len(text.split()) < words:
        text += " ".join(rng.sample(POOL, k=rng.randint(3, len(POOL)))) + " "
    return text


def test_the_everyday_word_mix_flags_a_scene_written_differently():
    usual = baseline([measure(_varied(seed, 1600)) for seed in range(8)])
    assert usual.deltas is not None and usual.delta_scenes == 8
    assert "voice_everyday_words" not in {
        note.code for note in drift(measure(_varied(99, 1600)), usual)
    }
    stranger = _scene(
        "Upon arrival, an assessment of conditions was undertaken by personnel; findings "
        "indicated deterioration of infrastructure, necessitating remediation within "
        "specified timeframes, pursuant to regulations governing maritime facilities. "
        "Therefore it is to be noted that all of the vessels are to be inspected by us. ",
        1600,
    )
    codes = {note.code for note in drift(measure(stranger), usual)}
    assert "voice_everyday_words" in codes


def test_gradual_drift_across_scenes_is_a_trend():
    early = [("s", measure(_scene(THIRD_PAST, 400))) for _ in range(3)]
    middle = [("s", measure(_scene(THIRD_PAST, 400))) for _ in range(2)]
    late = [("s", measure(_scene(FIRST_PRESENT, 400))) for _ in range(3)]
    found = trends([*early, *middle, *late])
    assert any(item.code == "voice_trend_sentence_mean" for item in found)
    assert trends(early) == []


def test_a_long_book_is_measured_quickly():
    scenes = [_scene(THIRD_PAST, 3000) for _ in range(40)]
    started = time.perf_counter()
    measured = [measure(text) for text in scenes]
    usual = baseline(measured)
    for item in measured:
        drift(item, usual)
    assert time.perf_counter() - started < 10


@pytest.mark.parametrize(
    "text",
    [
        "." * 399_999 + "x",
        "!?" * 200_000,
        "‘a’a " * 80_000,
        "a’" * 200_000,
        "\n" + " " * 399_998 + "x",
        '@"' * 200_000,
        "‘" * 400_000,
        "‘’a" * 130_000,
        "“" * 400_000,
    ],
    ids=[
        "dots",
        "marks",
        "single-quotes",
        "apostrophes",
        "blank-line",
        "at-quotes",
        "open-quotes",
        "open-close-quotes",
        "curly-doubles",
    ],
)
def test_the_longest_text_of_marks_alone_is_measured_in_well_under_a_second(text):
    # Patterns that tried again from every dot or quote took minutes on these.
    started = time.perf_counter()
    measure(text)
    assert time.perf_counter() - started < 0.5


def test_a_run_of_end_marks_still_ends_one_sentence_and_single_quotes_stay_in_a_paragraph():
    # "Wait...", "what?!", "She left.”" and the words after them.
    stats = measure("Wait... what?! She left.\u201d Then nothing")
    assert stats.sentences == 4
    # An opening quote with no close in its paragraph is not dialogue past the paragraph.
    text = "‘Go,’ she said.\n\nIt was Mara’s boat. ‘Stay\nhere,’ he said."
    mask = dialogue_mask(text)
    assert mask[text.index("Go")] and not mask[text.index("boat")]
    assert not mask[text.index("Stay")]


# ---------------------------------------------------------------- relationships


def _range(*present: set[str]) -> list[RangeScene]:
    return [RangeScene(f"s{i}", f"Scene {i}", frozenset(p)) for i, p in enumerate(present)]


def test_a_relationship_that_changes_while_the_pair_is_apart_is_noted():
    scenes = _range({"m", "t"}, {"m"}, {"t"}, {"m", "t"})
    facts = [PairFact("f1", ("m", "t"), "Mara trusts Tobin")]

    def standing(fact_id: str, index: int):
        return "holds" if index < 2 else "not_then"

    (row,) = relationship_drift(scenes, facts, {"m": "Mara", "t": "Tobin"}, standing)
    assert row.together == ["s0", "s3"]
    assert any("off the page" in note for note in row.notes)


def test_a_change_between_neighbouring_scenes_is_left_to_the_page():
    scenes = _range({"m", "t"}, {"m", "t"})
    facts = [PairFact("f1", ("m", "t"), "Mara trusts Tobin")]
    rows = relationship_drift(
        scenes, facts, {"m": "Mara", "t": "Tobin"}, lambda f, i: "holds" if i == 0 else "not_then"
    )
    assert rows[0].notes == []


def test_a_pair_that_stops_sharing_scenes_drifts_apart():
    scenes = _range({"m", "t"}, {"m", "t"}, {"m"}, {"t"}, {"m"}, {"t"}, {"m"})
    facts = [PairFact("f1", ("m", "t"), "Mara trusts Tobin")]
    (row,) = relationship_drift(scenes, facts, {"m": "Mara", "t": "Tobin"}, lambda f, i: "holds")
    assert any("none of the last 5" in note for note in row.notes)


def test_unclear_times_never_make_a_note():
    scenes = _range({"m", "t"}, {"m"}, {"m", "t"})
    facts = [PairFact("f1", ("m", "t"), "Mara trusts Tobin")]
    (row,) = relationship_drift(scenes, facts, {"m": "Mara", "t": "Tobin"}, lambda f, i: "unclear")
    assert row.notes == []
