import pytest

from backend.adapters.graph import _to_node

pytestmark = pytest.mark.unit


def test_to_node_maps_flat_manager_dict() -> None:
    node = _to_node({"id": "char_arin", "type": "Character", "name": "Arin"})

    assert node.id == "char_arin"
    assert node.type == "Character"
    assert node.properties == {"name": "Arin"}


def test_to_node_keeps_embedding_out_of_properties() -> None:
    node = _to_node({"id": "n1", "type": "Character", "name": "A", "embedding": [1.0, 2.0]})

    assert node.embedding == [1.0, 2.0]
    assert "embedding" not in node.properties


def test_to_node_derives_type_from_labels_when_absent() -> None:
    node = _to_node({"id": "n1", "labels": ["Entity", "Location"], "name": "Gate"})

    assert node.type == "Location"
    assert node.properties == {"name": "Gate"}
