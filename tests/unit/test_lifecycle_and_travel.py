"""Lasting statuses (CON-004) and travel read from a scene's prose (SCN-007): notes only,
only where a status is certainly true, an event that ends a status honoured, and a move
read only from a name, a verb of going in the simple past and a known place."""

from __future__ import annotations

import time

import pytest

from backend.domain.continuity import (
    COVERAGE,
    HowLong,
    StoryParts,
    TravelTime,
    movement_context,
)
from backend.domain.movement import (
    Elsewhere,
    Kept,
    MovementBrief,
    PlaceKnown,
    find_moves,
    movement_findings,
)
from backend.domain.worldtime import At, Between
from tests.unit.test_continuity import PEOPLE, _codes, _entity, _event, _fact, _run, _scene

pytestmark = pytest.mark.unit

STATUS = "HAS_STATUS"


def _value(word: str) -> dict:
    return {"kind": "string", "value": word}


# ------------------------------------------------------------------ lasting statuses


def test_someone_held_at_one_place_is_noted_elsewhere_until_released() -> None:
    held = _fact("f_held", "mara", "IMPRISONED_AT", "keep", starts=At(at=2), ends=At(at=9))
    scenes = [
        _scene("market", At(at=4), place="harbor", who=["mara"]),
        _scene("cell", At(at=5), place="keep", who=["mara"]),
        _scene("later", At(at=12), place="harbor", who=["mara"]),
    ]
    found = [item for item in _run(facts=[held], scenes=scenes) if item.check == "lifecycle"]
    (note,) = found
    assert note.code == "held_elsewhere" and note.severity == "note"
    assert note.scene_ids == ("market",)
    assert "Mara is held at Saltkeep" in note.message and "Lantern Harbor" in note.message

    # A release with her in it ends it from when it happens.
    release = _event("freed", At(at=3), kind="released", who=["mara"])
    after = _run(facts=[held], scenes=scenes, events=[release])
    assert "held_elsewhere" not in _codes(after)


def test_loose_times_and_untimed_statuses_never_count() -> None:
    held = _fact("f_held", "mara", "IMPRISONED_AT", "keep", starts=At(at=2), ends=At(at=9))
    loose = _scene("market", Between(earliest=1, latest=12), place="harbor", who=["mara"])
    assert "held_elsewhere" not in _codes(_run(facts=[held], scenes=[loose]))
    untimed = _fact("f_held", "mara", "IMPRISONED_AT", "keep")
    timed = _scene("market", At(at=4), place="harbor", who=["mara"])
    assert "held_elsewhere" not in _codes(_run(facts=[untimed], scenes=[timed]))


def test_exiled_from_a_place_and_there_is_a_note_until_their_return() -> None:
    exile = _fact("f_ex", "mara", "EXILED_FROM", "harbor", starts=At(at=1))
    scene = _scene("homecoming", At(at=6), place="harbor", who=["mara"])
    (note,) = [item for item in _run(facts=[exile], scenes=[scene]) if item.check == "lifecycle"]
    assert note.code == "exile_returned" and "exiled from Lantern Harbor" in note.message
    back = _event("return", At(at=5), kind="returned", who=["mara"])
    assert "exile_returned" not in _codes(_run(facts=[exile], scenes=[scene], events=[back]))


def test_someone_missing_in_a_scene_with_others_is_said_once_more_exactly() -> None:
    missing = _fact("f_m", "mara", STATUS, _value("missing"), starts=At(at=1), ends=At(at=9))
    with_ines = _scene("inn", At(at=4), place="tavern", who=["mara", "ines"])
    alone = _scene("woods", At(at=5), place="keep", who=["mara"])
    found = _run(facts=[missing], scenes=[with_ines, alone])
    codes = _codes(found)
    assert codes.count("missing_seen") == 1
    # The plain "should show it" note is said for the scene alone, not again for the inn.
    plain = [item for item in found if item.code == "condition_then"]
    assert [item.scene_ids for item in plain] == [("woods",)]
    found_event = _event("found", At(at=3), kind="found", who=["mara", "ines"])
    after = _run(facts=[missing], scenes=[with_ines, alone], events=[found_event])
    assert "missing_seen" not in _codes(after) and "condition_then" not in _codes(after)


def test_a_wedding_while_married_and_two_spouses_at_once_are_notes() -> None:
    more = [_entity("tobin", "Character")]
    first = _fact("f_1", "mara", "MARRIED_TO", "ines", starts=At(at=1))
    second = _fact("f_2", "mara", "MARRIED_TO", "tobin", starts=At(at=5))
    wedding = _event("vows", At(at=6), kind="wedding", who=["mara", "tobin"])
    found = _run(facts=[first, second], events=[wedding], more=more)
    assert set(_codes(found)) >= {"marries_while_married", "two_spouses"}
    assert all(item.severity == "note" for item in found if item.check == "lifecycle")
    # A divorce, or the death of the one married, ends it first.
    divorce = _event("parting", At(at=3), kind="divorce", who=["mara", "ines"])
    after = _run(facts=[first, second], events=[wedding, divorce], more=more)
    assert not {"marries_while_married", "two_spouses"} & set(_codes(after))
    death = _event("loss", At(at=4), kind="death", who=["ines"])
    after = _run(facts=[first, second], events=[wedding, death], more=more)
    assert not {"marries_while_married", "two_spouses"} & set(_codes(after))


def test_the_lifecycle_check_is_described_to_the_author() -> None:
    (lifecycle,) = [item for item in COVERAGE if item.check == "lifecycle"]
    assert "released" in lifecycle.covers and "never counts" in lifecycle.covers


# ------------------------------------------------------------------ travel from prose

PLACES = [
    PlaceKnown("harbor", "Lantern Harbor", ("Lantern Harbor", "the Harbor")),
    PlaceKnown("fennick", "Fennick", ("Fennick",)),
    PlaceKnown("keep", "Saltkeep", ("Saltkeep",)),
]
PEOPLE_IN = {"Wren": ("Wren", "Wren Ashe"), "Arlen": ("Arlen",)}


def _moves(text: str) -> list[tuple[str, str]]:
    return [(move.who, move.place) for move in find_moves(text, PEOPLE_IN, PLACES)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Wren rode to Fennick.", [("Wren", "fennick")]),
        ("At dawn Wren Ashe rode hard back to Fennick.", [("Wren", "fennick")]),
        ("Wren and Arlen sailed to Saltkeep.", [("Wren", "keep"), ("Arlen", "keep")]),
        ("Wren, alone, arrived at Lantern Harbor at noon.", [("Wren", "harbor")]),
        ("Wren reached Fennick before the rain.", [("Wren", "fennick")]),
        # Not said to happen, said aloud, set out only, a pronoun, or an unknown place.
        ("Wren did not ride to Fennick.", []),
        ("Wren would ride to Fennick in the morning.", []),
        ("Wren wanted to ride to Fennick.", []),
        ("Wren had ridden to Fennick once.", []),
        ("Wren set out for Fennick.", []),
        ("Wren rode towards Fennick.", []),
        ("She rode to Fennick.", []),
        ("“Wren rode to Fennick,” Arlen said.", []),
        ("Wren rode to Dunmore.", []),
        ("Wren's horse ran to Fennick.", []),
        ("Did Wren ride to Fennick?", []),
    ],
)
def test_a_move_is_read_only_where_the_words_say_it_for_certain(text, expected) -> None:
    assert _moves(text) == expected


def _brief(**fields) -> MovementBrief:
    return MovementBrief(people=dict(PEOPLE_IN), places=list(PLACES), **fields)


def test_a_move_away_from_the_plans_place_is_a_note_quoting_the_words() -> None:
    text = "The tide turned. Wren rode to Fennick."
    (note,) = movement_findings(text, _brief(scene_place="harbor"))
    assert note.code == "moves_elsewhere"
    assert "“Wren rode to Fennick”" in note.message and "Lantern Harbor" in note.message
    assert text[note.start : note.end] == "Wren rode to Fennick"
    # A place inside the planned one (a travel time of 0) is the same place.
    inside = _brief(scene_place="harbor", same={"fennick": "harbor", "harbor": "harbor"})
    assert movement_findings(text, inside) == []


def test_a_journey_quicker_than_the_table_allows_is_a_note() -> None:
    text = "Wren rode to Fennick."
    brief = _brief(
        travel=[("fennick", "keep", 3.0, "3 days")],
        elsewhere=[Elsewhere("Wren", "keep", "the scene “The Vault” (day 6)", 1.0, "1 day", True)],
    )
    (note,) = movement_findings(text, brief)
    assert note.code == "travel_too_soon"
    assert "at most 1 day later" in note.message and "takes 3 days" in note.message
    slow = _brief(
        travel=[("fennick", "keep", 3.0, "3 days")],
        elsewhere=[Elsewhere("Wren", "keep", "the scene “The Vault”", 4.0, "4 days", True)],
    )
    assert movement_findings(text, slow) == []


def test_a_move_while_held_or_into_exile_is_a_note() -> None:
    held = _brief(kept=[Kept("Wren", "held", "Wren is imprisoned at Saltkeep", "keep")])
    (note,) = movement_findings("Wren walked to Fennick.", held)
    assert note.code == "moves_while_held" and "nothing has freed them" in note.message
    # Taken to the place they are held: nothing to say.
    assert movement_findings("Wren walked to Saltkeep.", held) == []
    exiled = _brief(kept=[Kept("Wren", "exiled", "Wren is exiled from Fennick", "fennick")])
    (note,) = movement_findings("Wren walked to Fennick.", exiled)
    assert note.code == "moves_into_exile"


def test_movement_context_reads_nearest_appearances_and_what_keeps_them() -> None:
    held = _fact("f_held", "mara", "IMPRISONED_AT", "keep", starts=At(at=2), ends=At(at=9))
    parts = StoryParts(
        entities={item.id: item for item in PEOPLE},
        facts=[held],
        events=[],
        scenes=[
            _scene("before", At(at=3), place="harbor", who=["mara"]),
            _scene("now", At(at=5), place="harbor", who=["mara"]),
            _scene("after", At(at=6), place="tavern", who=["mara"]),
            _scene("much_later", At(at=20), place="keep", who=["mara"]),
        ],
        unit="day",
        how_long=HowLong(travel=[TravelTime(from_id="harbor", to_id="tavern", amount=2)]),
    )
    context = movement_context(parts, "now", ["mara"])
    assert {(item.where.split("”")[0], item.after) for item in context.elsewhere} == {
        ("the scene “Before", False),
        ("the scene “After", True),
    }
    (kept,) = context.kept
    assert kept.kind == "held" and kept.place_id == "keep"
    assert context.travel == [("harbor", "tavern", 2.0, "2 days")]
    assert "harbor" in context.places and "mara" not in context.places


def test_reading_a_500_000_character_scene_is_linear() -> None:
    """The PERFORMANCE RULE: a long scene of near misses and moves reads in well under a
    second, so travel read from the words never stalls the server."""
    chunk = (
        "Wren rode hard back to Fennick. Arlen wanted to sail to Saltkeep, but did not. "
        "She walked to the market where nobody went, and Wren, alone, arrived at Lantern "
        "Harbor. “Wren rode to Fennick,” said someone. Rode rode rode to to to the the. "
    )
    text = (chunk * (500_000 // len(chunk) + 1))[:500_000]
    brief = _brief(scene_place="harbor", kept=[Kept("Wren", "held", "Wren is held", None)])
    started = time.perf_counter()
    found = movement_findings(text, brief)
    took = time.perf_counter() - started
    assert found  # notes deduplicated per kind of note
    assert took < 2.0, took
    # And twice as long takes about twice as long: no worse than linear.
    started = time.perf_counter()
    movement_findings(text + text, brief)
    assert time.perf_counter() - started < max(4 * took, 0.5)
