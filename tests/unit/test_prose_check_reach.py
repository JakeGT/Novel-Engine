"""What the prose checks read and how fast (SCN-007, CON-004, voice): counts tied to whoever
holds the thing, heights only from words that state one, ages that are this scene's, and
texts far longer or with far more people than a scene still read in well under seconds."""

from __future__ import annotations

import time

import pytest

from backend.domain.appearance import AgeKnown, AppearanceDetail, heights_in, identity_clashes
from backend.domain.drafting import DraftBrief, Holding, Supply, supply_findings
from backend.domain.voice import attribute, speeches

TALL = [AppearanceDetail("Wren", ("Wren",), "Wren is tall, with a scar on her left cheek")]
AGE = [AgeKnown("Wren", ("Wren",), 16, 16, "born year 5, scene year 21")]
WRENS_ARROWS = Supply("Wren's arrows", ("arrows",), 5)
ARROWS = Supply("the arrows", ("arrows",), 5)


def _spent(text: str, supply: Supply = WRENS_ARROWS, **fields) -> list[str]:
    brief = DraftBrief(people=("Wren", "Kell"), supplies=[supply], **fields)
    return [finding.code for finding in supply_findings(text, brief)]


# ------------------------------------------------------------------ whose count it is


@pytest.mark.parametrize(
    "text",
    [
        "Wren watched as the guards fired twenty arrows.",
        "Kell shot six arrows at Wren.",
        "Wren saw that the enemy had nine arrows left.",
        "Wren's brother fired six arrows.",
    ],
)
def test_arrows_spent_or_had_by_someone_else_are_not_hers(text: str) -> None:
    assert _spent(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Wren fired six arrows.",
        "Wren waited. She fired six arrows.",
        "Wren turned, fired six arrows.",
        "In the dark, Wren fired six arrows.",
    ],
)
def test_the_holder_spending_by_name_or_pronoun_is_counted(text: str) -> None:
    assert _spent(text) == ["supply_overspent"]


def test_the_holder_the_story_bible_gives_is_the_one_counted() -> None:
    held = [Holding("the arrows", ("the arrows",), "Kell", ("Kell",))]
    assert _spent("Wren fired six arrows.", ARROWS, holdings=held) == []
    assert _spent("Kell fired six arrows.", ARROWS, holdings=held) == ["supply_overspent"]


def test_with_no_holder_known_anyone_in_the_scene_spends_them() -> None:
    assert _spent("Kell fired six arrows.", ARROWS) == ["supply_overspent"]
    assert _spent("The guards fired six arrows.", ARROWS) == []


# ------------------------------------------------------------------ heights


@pytest.mark.parametrize(
    "words",
    ["A small scar above her left eye", "a tiny tattoo on her right wrist", "a short skirt"],
)
def test_a_mark_or_clothes_in_the_details_is_not_a_height(words: str) -> None:
    assert heights_in(words) == []


@pytest.mark.parametrize(
    "text",
    [
        "Wren was short with him.",
        "Wren was short on patience that night.",
        "Wren was a small part of the plan.",
        "Wren was a tiny bit annoyed.",
        "Wren was small comfort to them.",
        "Wren was short a few coins.",
    ],
)
def test_idioms_with_height_words_are_not_her_height(text: str) -> None:
    assert identity_clashes(text, TALL) == []


@pytest.mark.parametrize(
    "text",
    [
        "Wren was short.",
        "Wren was a small woman.",
        "Wren, short and pale, ran.",
        "The tiny Wren ran.",
    ],
)
def test_a_statement_of_her_height_is_still_read(text: str) -> None:
    assert [clash.kind for clash in identity_clashes(text, TALL)] == ["height"]


# ------------------------------------------------------------------ ages


@pytest.mark.parametrize(
    "text", ["Wren was ten that summer.", "Wren, two, three, and four ran together."]
)
def test_another_time_or_a_list_is_not_her_age(text: str) -> None:
    assert identity_clashes(text, [], AGE) == []


def test_an_age_in_this_scene_is_still_read() -> None:
    assert [c.said for c in identity_clashes("Wren, sixty, was the oldest.", [], AGE)] == ["60"]


# ------------------------------------------------------------------ speed


def test_a_long_run_on_text_is_counted_well_under_two_seconds() -> None:
    # Each count looked back over the whole sentence for its verb: 80,000 characters took
    # over ten seconds.
    texts = [
        "Wren paid a coin, kept two coins, gave away one coin; " * 9_300,
        "Wren " + "fired three arrows " * 26_000,
        "Wren fired " + "a arrow " * 62_000,
    ]
    brief = DraftBrief(people=("Wren",), supplies=[Supply("coins", ("coins",), 5), ARROWS])
    started = time.perf_counter()
    for text in texts:
        assert len(text) >= 490_000
        supply_findings(text, brief)
    assert time.perf_counter() - started < 2


def test_many_sentences_counted_one_by_one_stay_fast() -> None:
    text = "Wren fired an arrow, and Kell fired two arrows; Wren had one arrow left. " * 6_000
    started = time.perf_counter()
    supply_findings(text, DraftBrief(people=("Wren", "Kell"), supplies=[ARROWS]))
    assert time.perf_counter() - started < 2


def test_a_mark_in_every_sentence_is_read_well_under_two_seconds() -> None:
    # Each mark reread every sentence for the person's pronoun: 2,000 sentences took 11s.
    text = "Wren touched the scar on her left cheek. " * 4_000
    started = time.perf_counter()
    assert identity_clashes(text, TALL) == []
    assert time.perf_counter() - started < 2


def test_a_mark_on_the_other_side_is_still_found_in_a_long_text() -> None:
    text = (
        "Wren touched the scar on her left cheek. " * 50
        + "Wren touched the scar on her right cheek."
    )
    assert [clash.kind for clash in identity_clashes(text, TALL)] == ["mark"]


def test_dialogue_with_three_hundred_people_is_attributed_quickly() -> None:
    # A pattern was built for every quote and every person: 300 people took over a minute.
    people = {f"Person{i} Surname{i}": (f"Person{i} Surname{i}", f"Person{i}") for i in range(300)}
    turns = [
        '"I do not think so, not today," said Person1. She looked away.',
        '"Then when?" Person2 asked.',
        "Rain fell on the roofs of the town and the gutters ran full.",
        '"Tomorrow, maybe."',
    ]
    scene = "\n\n".join(turns[i % 4] for i in range(400))
    started = time.perf_counter()
    found, _share = speeches(scene, people)
    assert time.perf_counter() - started < 5
    assert set(found) == {"Person1 Surname1", "Person2 Surname2"}


def test_one_paragraph_of_many_quotes_is_attributed_quickly() -> None:
    text = '"a," said Person1. ' * 20_000
    started = time.perf_counter()
    lines = attribute(text, {"Person1": ("Person1",)})
    assert time.perf_counter() - started < 3
    assert len(lines) == 20_000
