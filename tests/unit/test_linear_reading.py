"""The PERFORMANCE RULE for reading long texts: what is said aloud, travel, review units,
parts and fences are each found in time that grows with the text's length, never with
its square. Each speed test is on a 500,000 character text that took many seconds (or
minutes) before, with a generous bound."""

from __future__ import annotations

import random
import re
import time

import pytest

from backend.domain.check_passes import CheckPass, PassMaterial
from backend.domain.drafting import _first_person, _without_speech
from backend.domain.movement import PlaceKnown, find_moves
from backend.domain.prompting import fence
from backend.domain.review_bundles import SceneFrame, review_units
from backend.domain.spoken import blank_spoken, spoken_spans
from backend.services.scene_checks import in_parts

pytestmark = pytest.mark.unit

SIZE = 500_000
# The pattern speech was found by before: right, but reading to the end of the text from
# every quote never closed.
_OLD_SPOKEN = re.compile(r"\"[^\"]*\"|“[^”]*”|‘(?:[^’]|’(?=\w))*’(?!\w)")


def _timed(work, *args, **kwargs):
    started = time.perf_counter()
    result = work(*args, **kwargs)
    return result, time.perf_counter() - started


def _old_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _OLD_SPOKEN.finditer(text)]


# ------------------------------------------------------------------ what is said aloud


@pytest.mark.parametrize(
    "text",
    [
        "“I will go,” said Wren. She went.",
        '"I will," he said. "Now?" "Yes."',
        "‘Wren’s horse is lame,’ said Arlen. ‘It isn’t far.’",
        "The dogs’ bowls were empty. ‘Fill them,’ she said.",
        "‘Rock ’n’ roll,’ he said, and ‘no’.",
        'An open “quote never closed, and ‘another, and "one more.',
        "‘nested “double” inside’ and “single ‘inside’ double”.",
        "Ends with a closer’",
        "",
        "No speech at all.",
    ],
)
def test_speech_is_found_exactly_where_the_old_pattern_found_it(text: str) -> None:
    assert spoken_spans(text) == _old_spans(text)
    assert blank_spoken(text) == _OLD_SPOKEN.sub(lambda m: " " * len(m.group(0)), text)


def test_speech_matches_the_old_pattern_on_random_text() -> None:
    alphabet = ["a", "b", " ", "\n", ".", "1", '"', "“", "”", "‘", "’", "_"]
    rng = random.Random(7)
    for _ in range(3_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert spoken_spans(text) == _old_spans(text), text


@pytest.mark.parametrize("quote", ["‘", "“", '"'])
def test_speech_in_500_000_characters_of_quotes_never_closed_is_read_quickly(quote) -> None:
    text = ((quote + "Wren went ") * (SIZE // 11 + 1))[:SIZE]
    blanked, took = _timed(blank_spoken, text)
    assert len(blanked) == len(text)
    assert took < 2.0, took
    # The checks of a draft that blank speech read it as quickly.
    _, took = _timed(_without_speech, text)
    assert took < 2.0, took
    _, took = _timed(_first_person, text)
    assert took < 2.0, took


# ------------------------------------------------------------------ travel

PLACES = [PlaceKnown("fennick", "Fennick", ("Fennick",))]


def test_a_long_sentence_of_many_journeys_is_read_quickly() -> None:
    """One sentence of 500,000 characters with a journey every few words: each journey
    looks up its sentence's hedges instead of reading the sentence again."""
    text = ("Wren rode to Fennick and " * (SIZE // 25 + 1))[:SIZE]
    moves, took = _timed(find_moves, text, {"wren": ("Wren",)}, PLACES)
    assert len(moves) == SIZE // 25
    assert took < 3.0, took
    hedged = "Wren would " + text
    moves, took = _timed(find_moves, hedged, {"wren": ("Wren",)}, PLACES)
    assert moves == [] and took < 3.0, took


@pytest.mark.parametrize("quote", ["‘", "“"])
def test_travel_in_500_000_characters_of_quotes_never_closed_is_read_quickly(quote) -> None:
    text = ((quote + "Wren rode to Fennick. ") * (SIZE // 23 + 1))[:SIZE]
    _, took = _timed(find_moves, text, {"wren": ("Wren",)}, PLACES)
    assert took < 3.0, took


# ------------------------------------------------------------------ review units and parts


def test_review_units_of_a_long_scene_with_many_names_are_found_quickly() -> None:
    paragraph = "Mara saw Ines by the boats.\n\n"
    text = (paragraph * (SIZE // len(paragraph) + 1))[:SIZE]
    mentions = []
    for start in range(0, len(text) - len(paragraph), len(paragraph)):
        mentions.append((start, start + 4, "mara"))
        mentions.append((start + 9, start + 13, "ines"))
    types = {"mara": "Character", "ines": "Character"}
    units, took = _timed(review_units, text, SceneFrame("s1", 0), mentions, types)
    assert len(units) >= SIZE // len(paragraph)
    assert units[0].entity_ids == frozenset({"mara", "ines"})
    assert units[-1].entity_ids <= frozenset({"mara", "ines"})
    assert took < 5.0, took


def test_review_units_name_only_the_entries_in_each_unit_as_before() -> None:
    text = "Mara waited.\n\nInes came.\n\nNobody spoke."
    mentions = [(14, 18, "ines"), (0, 4, "mara"), (2, 3, "mara")]
    units = review_units(text, SceneFrame("s1", 0), mentions, {})
    assert [unit.entity_ids for unit in units] == [
        frozenset({"mara"}),
        frozenset({"ines"}),
        frozenset(),
    ]


def test_a_long_scene_is_cut_into_parts_quickly() -> None:
    """Many short passages in one part: its size is kept as it grows, not added up
    again for each passage."""
    text = ("Rain fell.\n\n" * (SIZE // 12 + 1))[:SIZE]
    material = PassMaterial(CheckPass.CLARITY, story="- Rain falls in Fennick.")
    grouped, took = _timed(in_parts, "s1", text, material, budget=10_000_000)
    assert grouped.whole == "One part would hold it all, so it was sent whole."
    assert took < 5.0, took


def test_parts_hold_as_many_passages_as_fit_as_before() -> None:
    text = "\n\n".join(f"Passage {n} has some words in it." for n in range(120))
    material = PassMaterial(CheckPass.CLARITY, story="- A fact.")
    grouped = in_parts("s1", text, material, budget=600)
    sizes = [len(call.passages.split("\n\n")) for call in grouped.calls]
    assert sum(sizes) == 120 and len(sizes) > 2
    # Each part but the last is full: one more passage would not have fitted.
    assert all(size == sizes[0] for size in sizes[:-1])


# ------------------------------------------------------------------ fences


def test_a_fence_skips_every_marker_already_written_in_one_pass() -> None:
    used = "".join(f"<<<SCENE-{number}>>>" for number in range(1, 40_000))
    text = (used + " " * SIZE)[: max(SIZE, len(used))]
    (start, end), took = _timed(fence, "SCENE", "nothing here", text)
    assert (start, end) == ("<<<SCENE-40000>>>", "<<<END SCENE-40000>>>")
    assert took < 1.0, took


def test_a_fence_is_the_first_number_free_in_every_text_as_before() -> None:
    assert fence("A", "plain") == ("<<<A-1>>>", "<<<END A-1>>>")
    assert fence("A", "<<<A-1>>>", "<<<END A-2>>>") == ("<<<A-3>>>", "<<<END A-3>>>")
    # A different number that starts the same way, or a lookalike label, is not the same.
    assert fence("A", "<<<A-11>>> <<<A-01>>>") == ("<<<A-1>>>", "<<<END A-1>>>")
    assert fence("A", "<<<BA-1>>>") == ("<<<A-2>>>", "<<<END A-2>>>")
