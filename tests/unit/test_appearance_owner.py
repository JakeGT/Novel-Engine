"""A possessive ties a colour to someone only when it owns the eyes or hair itself
(CON-004): "Wren's grey eyes", not "Wren's mother had green eyes"."""

from __future__ import annotations

import pytest

from backend.domain.appearance import AppearanceDetail, clashes

pytestmark = pytest.mark.unit

WREN = AppearanceDetail("Wren Halloway", ("Wren Halloway", "Wren"), "Blue eyes; brown hair.")


@pytest.mark.parametrize(
    "sentence",
    [
        "Wren's mother had green eyes.",
        "Wren's mother's eyes were green.",
        "Wren's sister's green eyes narrowed.",
        "Wren's brother, green-eyed and silent, waited.",
        "Wren's old horse had black hair.",
        "Wren's coat was grey, and the boy's green eyes followed her.",
    ],
)
def test_someone_elses_eyes_after_a_possessive_are_not_hers(sentence: str) -> None:
    assert clashes(sentence, [WREN], ["Osric Pell"]) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Wren's green eyes narrowed.",
        "Wren's tired green eyes narrowed.",
        "Wren's tired, green eyes narrowed.",
        "Wren's eyes were green.",
        "Wren's long black hair whipped in the wind.",
        "Wren's hood hid her black hair.",
    ],
)
def test_her_own_eyes_and_hair_after_a_possessive_are_read(sentence: str) -> None:
    assert len(clashes(sentence, [WREN], ["Osric Pell"])) == 1
