import pytest

from backend.graph.resolver import extract_entity_ids

pytestmark = pytest.mark.unit


def test_extracts_entity_ids_in_source_order() -> None:
    text = (
        "[Arin](entity:Character:char_arin) visits [King's Gate](entity:Location:loc_kings_gate)."
    )

    assert extract_entity_ids(text) == ["char_arin", "loc_kings_gate"]


def test_deduplicates_entity_ids_keeping_first_occurrence() -> None:
    text = (
        "[Arin](entity:Character:char_arin) sees "
        "[Arin again](entity:Character:char_arin) and "
        "[Gate](entity:Location:loc_gate)."
    )

    assert extract_entity_ids(text) == ["char_arin", "loc_gate"]


def test_ignores_plain_markdown_links() -> None:
    assert extract_entity_ids("Read [notes](https://example.test/notes).") == []


def test_empty_input_has_no_entities() -> None:
    assert extract_entity_ids("") == []


def test_ignores_malformed_entity_tags() -> None:
    assert extract_entity_ids("[Arin](entity:Character)") == []
    assert extract_entity_ids("[Arin](entity:Character:char_arin") == []
    assert extract_entity_ids("entity:Character:char_arin") == []
    assert extract_entity_ids("[Arin](entity::char_arin)") == []


def test_greedy_regex_can_span_malformed_fragments() -> None:
    """Characterizes current behavior: the tag regex is greedy, so adjacent
    malformed fragments can be captured as a single spurious ID."""

    text = (
        "[Arin](entity:Character)"
        "[Arin](entity:Character:char_arin"
        "entity:Character:char_arin"
        "[Arin](entity::char_arin)"
    )

    assert extract_entity_ids(text) == [
        "Character:char_arinentity:Character:char_arin[Arin](entity::char_arin"
    ]


def test_supports_unicode_display_names_and_ids() -> None:
    text = "[Ærin Ólafsson](entity:Character:char_ærin) at [Ōsaka](entity:Location:loc_ōsaka)"

    assert extract_entity_ids(text) == ["char_ærin", "loc_ōsaka"]


def test_preserves_safe_punctuation_inside_ids() -> None:
    text = "[Node](entity:Concept:concept-1.2_3)"

    assert extract_entity_ids(text) == ["concept-1.2_3"]


def test_adjacent_tags_are_extracted_independently() -> None:
    text = "[A](entity:Character:char_a)[B](entity:Character:char_b)"

    assert extract_entity_ids(text) == ["char_a", "char_b"]
