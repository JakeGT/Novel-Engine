import json

import pytest

from backend.graph.schema import EdgeType, NodeType, SchemaRegistry

pytestmark = pytest.mark.unit


def _registry(tmp_path, name: str = "schema.json") -> SchemaRegistry:
    return SchemaRegistry(config_path=str(tmp_path / name))


def test_recognizes_core_node_and_edge_types(tmp_path) -> None:
    registry = _registry(tmp_path)

    assert registry.is_valid_node_type(NodeType.CHARACTER.value)
    assert registry.is_valid_node_type("Location")
    assert registry.is_valid_edge_type(EdgeType.KNOWS.value)
    assert not registry.is_valid_node_type("NotARealType")
    assert not registry.is_valid_edge_type("NOT_A_REAL_EDGE")


def test_custom_types_persist_to_disk_and_reload(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.add_custom_node_type("Starship")
    registry.add_custom_edge_type("PILOTS")

    payload = json.loads((tmp_path / "schema.json").read_text(encoding="utf-8"))
    assert payload["node_types"] == ["Starship"]
    assert payload["edge_types"] == ["PILOTS"]

    reloaded = _registry(tmp_path)
    assert reloaded.is_valid_node_type("Starship")
    assert reloaded.is_valid_edge_type("PILOTS")


def test_adding_duplicate_custom_type_is_idempotent(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.add_custom_node_type("Starship")
    registry.add_custom_node_type("Starship")

    assert registry.custom_node_types == {"Starship"}


def test_core_types_are_not_stored_as_custom(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.add_custom_node_type("Character")
    registry.add_custom_edge_type("KNOWS")

    assert registry.custom_node_types == set()
    assert registry.custom_edge_types == set()


def test_removing_custom_type_updates_registry_and_disk(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.add_custom_node_type("Starship")
    registry.remove_custom_node_type("Starship")

    assert registry.custom_node_types == set()
    assert not registry.is_valid_node_type("Starship")
    assert json.loads((tmp_path / "schema.json").read_text(encoding="utf-8"))["node_types"] == []


def test_all_types_begin_with_declared_enum_order(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.add_custom_node_type("Starship")

    node_types = registry.get_all_node_types()
    declared = [member.value for member in NodeType]

    assert node_types[: len(declared)] == declared
    assert set(node_types[len(declared) :]) == {"Starship"}


def test_custom_type_validation_is_not_enforced_yet(tmp_path) -> None:
    """Characterizes current behavior: identifiers are recorded without validation."""

    registry = _registry(tmp_path)
    registry.add_custom_node_type("bad type!")

    assert registry.is_valid_node_type("bad type!")


def test_missing_config_file_starts_with_empty_custom_types(tmp_path) -> None:
    registry = SchemaRegistry(config_path=str(tmp_path / "does_not_exist.json"))

    assert registry.custom_node_types == set()
    assert registry.custom_edge_types == set()


def test_world_type_is_recognized(tmp_path) -> None:
    registry = _registry(tmp_path)

    assert registry.is_valid_node_type("World")


def test_custom_types_are_sorted_in_output_and_persistence(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.add_custom_node_type("Zeta")
    registry.add_custom_node_type("Alpha")
    registry.add_custom_edge_type("ZED")
    registry.add_custom_edge_type("ABC")

    declared = [member.value for member in NodeType]
    assert registry.get_all_node_types()[len(declared) :] == ["Alpha", "Zeta"]

    payload = json.loads((tmp_path / "schema.json").read_text(encoding="utf-8"))
    assert payload["node_types"] == ["Alpha", "Zeta"]
    assert payload["edge_types"] == ["ABC", "ZED"]
