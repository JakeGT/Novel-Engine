from datetime import UTC, datetime

import pytest

from backend.graph.schema import Edge, Node

pytestmark = pytest.mark.unit


def test_node_defaults_are_stable() -> None:
    node = Node(id="n1", type="Character")

    assert node.properties == {}
    assert node.embedding is None
    assert node.created_at.tzinfo is None
    assert isinstance(node.created_at, datetime)


def test_node_dump_uses_one_nested_properties_shape() -> None:
    node = Node(
        id="n1",
        type="Character",
        properties={"name": "Arin", "role": "scout"},
        embedding=[0.1, 0.2, 0.3],
    )

    dumped = node.model_dump()

    assert set(dumped) == {
        "id",
        "type",
        "properties",
        "embedding",
        "workspace_id",
        "project_id",
        "created_at",
    }
    assert dumped["id"] == "n1"
    assert dumped["type"] == "Character"
    assert dumped["properties"] == {"name": "Arin", "role": "scout"}
    assert dumped["embedding"] == [0.1, 0.2, 0.3]


def test_node_json_mode_serializes_timestamps_as_iso_strings() -> None:
    node = Node(id="n1", type="Character", created_at=datetime(2024, 1, 1, 12, 30, tzinfo=UTC))

    dumped = node.model_dump(mode="json")

    assert dumped["created_at"].startswith("2024-01-01T12:30:00")


def test_edge_dump_exposes_properties_and_validity_fields() -> None:
    edge = Edge(source="char_arin", target="loc_gate", type="LOCATED_AT", properties={"since": 1})

    dumped = edge.model_dump()

    assert set(dumped) == {
        "source",
        "target",
        "type",
        "properties",
        "workspace_id",
        "project_id",
        "valid_from",
        "valid_until",
    }
    assert dumped["source"] == "char_arin"
    assert dumped["target"] == "loc_gate"
    assert dumped["properties"] == {"since": 1}
    assert dumped["valid_from"] is None
    assert dumped["valid_until"] is None
