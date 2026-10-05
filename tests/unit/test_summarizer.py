import pytest

from backend.graph.summarizer import summarize_context

pytestmark = pytest.mark.unit


def test_empty_context_returns_empty_string() -> None:
    assert summarize_context([]) == ""


def test_nodes_are_ordered_by_relevance_score_descending() -> None:
    nodes = [
        {"id": "low", "type": "Concept", "relevance_score": 1.0, "properties": {"name": "Low"}},
        {"id": "high", "type": "Concept", "relevance_score": 9.0, "properties": {"name": "High"}},
        {"id": "mid", "type": "Concept", "relevance_score": 5.0, "properties": {"name": "Mid"}},
    ]

    summary = summarize_context(nodes)
    lines = summary.splitlines()

    assert lines[0] == "--- EXTERNAL KNOWLEDGE CONTEXT ---"
    assert lines[-1] == "----------------------------------"
    assert lines.index("[Concept] High (Relevance: 9.0)") < lines.index(
        "[Concept] Mid (Relevance: 5.0)"
    )
    assert lines.index("[Concept] Mid (Relevance: 5.0)") < lines.index(
        "[Concept] Low (Relevance: 1.0)"
    )


def test_missing_name_falls_back_to_id() -> None:
    nodes = [{"id": "char_arin", "type": "Character", "relevance_score": 1.0, "properties": {}}]

    summary = summarize_context(nodes)

    assert "[Character] char_arin (Relevance: 1.0)" in summary


def test_name_property_is_not_repeated_in_body() -> None:
    nodes = [
        {
            "id": "char_arin",
            "type": "Character",
            "relevance_score": 1.0,
            "properties": {"name": "Arin", "role": "scout"},
        }
    ]

    summary = summarize_context(nodes)

    assert "  - role: scout" in summary
    assert "  - name: Arin" not in summary


def test_nested_values_are_rendered_via_string_conversion() -> None:
    nodes = [
        {
            "id": "n1",
            "type": "System",
            "relevance_score": 1.0,
            "properties": {"rules": {"magic": "scarce"}},
        }
    ]

    summary = summarize_context(nodes)

    assert "  - rules: {'magic': 'scarce'}" in summary


def test_storage_only_properties_are_currently_included() -> None:
    """Characterizes current behavior: storage metadata is not filtered out."""

    nodes = [
        {
            "id": "n1",
            "type": "Concept",
            "relevance_score": 1.0,
            "properties": {"name": "Idea", "embedding": [0.1, 0.2]},
        }
    ]

    summary = summarize_context(nodes)

    assert "  - embedding: [0.1, 0.2]" in summary


def test_nodes_without_relevance_score_default_to_zero_and_keep_input_order() -> None:
    nodes = [
        {"id": "first", "type": "Concept", "properties": {"name": "First"}},
        {"id": "second", "type": "Concept", "properties": {"name": "Second"}},
    ]

    summary = summarize_context(nodes)
    lines = summary.splitlines()

    assert lines.index("[Concept] First (Relevance: 0)") < lines.index(
        "[Concept] Second (Relevance: 0)"
    )
