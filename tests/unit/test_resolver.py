from backend.graph.resolver import extract_entity_ids


def test_extracts_entity_ids_in_source_order() -> None:
    text = (
        "[Arin](entity:Character:char_arin) visits "
        "[King's Gate](entity:Location:loc_kings_gate)."
    )

    assert extract_entity_ids(text) == ["char_arin", "loc_kings_gate"]


def test_deduplicates_entity_ids() -> None:
    text = (
        "[Arin](entity:Character:char_arin) sees "
        "[Arin again](entity:Character:char_arin)."
    )

    assert extract_entity_ids(text) == ["char_arin"]


def test_ignores_plain_markdown_links() -> None:
    assert extract_entity_ids("Read [notes](https://example.test/notes).") == []


def test_empty_input_has_no_entities() -> None:
    assert extract_entity_ids("") == []
