"""Travel read from the words (SCN-007): journeys only dreamed or told in a clause about
something else are not read, and people are told apart by their entries, so two who
share a name are never mixed up."""

from __future__ import annotations

import pytest

from backend.domain.movement import (
    Elsewhere,
    Kept,
    MovementBrief,
    PlaceKnown,
    find_moves,
    movement_findings,
)

pytestmark = pytest.mark.unit

PLACES = [
    PlaceKnown("fennick", "Fennick", ("Fennick",)),
    PlaceKnown("keep", "Saltkeep", ("Saltkeep",)),
]
PEOPLE = {"ent_wren": ("Wren", "Wren Ashe"), "ent_arlen": ("Arlen",)}


def _moves(text: str, people=None) -> list[tuple[str, str]]:
    return [(m.who, m.place) for m in find_moves(text, people or PEOPLE, PLACES)]


@pytest.mark.parametrize(
    "text",
    [
        "The horse that Wren rode to Fennick was lame.",
        "The cart which Arlen drove to Saltkeep broke a wheel.",
        "Wren wandered to Fennick in her dreams.",
        "In his sleep Arlen walked to Saltkeep, and woke in his bed.",
        "Wren, dreaming, rode to Fennick.",
        "Arlen flew to Saltkeep in his imagination.",
    ],
)
def test_a_journey_dreamed_or_told_about_something_else_is_not_read(text: str) -> None:
    assert _moves(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Wren rode to Fennick.", [("ent_wren", "fennick")]),
        ("Wren rode to Fennick, where she had friends.", [("ent_wren", "fennick")]),
        ("Wren rode to Fennick. In her dreams she was home.", [("ent_wren", "fennick")]),
        ("Wren and Arlen sailed to Saltkeep.", [("ent_wren", "keep"), ("ent_arlen", "keep")]),
    ],
)
def test_a_plain_journey_is_still_read(text: str, expected) -> None:
    assert _moves(text) == expected


def test_people_are_keyed_by_their_entries_and_a_shared_name_is_not_read() -> None:
    twins = {"ent_wren_1": ("Wren",), "ent_wren_2": ("Wren", "Wren Vale")}
    # "Wren" could be either: not read. "Wren Vale" is only the second.
    assert _moves("Wren rode to Fennick.", twins) == []
    assert _moves("Wren Vale rode to Fennick.", twins) == [("ent_wren_2", "fennick")]


def test_what_keeps_one_person_is_never_read_against_another_of_the_same_name() -> None:
    brief = MovementBrief(
        people={"ent_wren": ("Wren",), "ent_kell": ("Kell",)},
        shown={"ent_wren": "Wren", "ent_kell": "Kell"},
        places=list(PLACES),
        # Another character also called Wren, not in the scene, is held at the keep.
        kept=[Kept("ent_other_wren", "held", "Wren is imprisoned at Saltkeep", "keep")],
        travel=[("fennick", "keep", 3.0, "3 days")],
        elsewhere=[
            Elsewhere("ent_other_wren", "keep", "the scene “The Cell” (day 6)", 1.0, "1 day", True)
        ],
    )
    assert movement_findings("Wren rode to Fennick.", brief) == []
    held = MovementBrief(
        people={"ent_wren": ("Wren",)},
        shown={"ent_wren": "Wren"},
        places=list(PLACES),
        kept=[Kept("ent_wren", "held", "Wren is imprisoned at Saltkeep", "keep")],
    )
    (note,) = movement_findings("Wren rode to Fennick.", held)
    # The note calls them by name; what it is about is kept by entry.
    assert note.code == "moves_while_held"
    assert note.message.startswith("Here the words take Wren to Fennick")
    assert note.subject.startswith("ent_wren\x1e")
