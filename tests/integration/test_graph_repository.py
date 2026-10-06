from collections.abc import Iterator

import pytest

from backend.adapters.graph import Neo4jGraphRepository
from backend.graph.manager import GraphManager
from backend.graph.schema import Edge, Node

pytestmark = pytest.mark.integration

WS = "ws-test"
OTHER = "ws-other"


@pytest.fixture
def repository(neo4j_driver, neo4j_settings) -> Iterator[Neo4jGraphRepository]:
    manager = GraphManager(neo4j_settings)
    try:
        yield Neo4jGraphRepository(manager)
    finally:
        manager.close()


def test_node_roundtrip_preserves_type_and_properties(repository: Neo4jGraphRepository) -> None:
    repository.add_node(
        Node(
            id="char_mara",
            type="Character",
            properties={"name": "Mara Voss", "age": 34},
            workspace_id=WS,
        )
    )

    node = repository.get_node("char_mara", workspace_id=WS)
    assert node is not None
    assert node.type == "Character"
    assert node.properties["name"] == "Mara Voss"
    assert node.properties["age"] == 34
    assert repository.get_node("missing", workspace_id=WS) is None


def test_update_node_merges_properties(repository: Neo4jGraphRepository) -> None:
    repository.add_node(
        Node(id="loc_harbor", type="Location", properties={"name": "Harbor"}, workspace_id=WS)
    )
    updated = repository.update_node(
        Node(id="loc_harbor", type="Location", properties={"population": 900}, workspace_id=WS)
    )

    assert updated is not None
    assert updated.type == "Location"
    assert updated.properties["name"] == "Harbor"
    assert updated.properties["population"] == 900


def test_edges_and_neighbors(repository: Neo4jGraphRepository) -> None:
    repository.add_node(
        Node(id="char_mara", type="Character", properties={"name": "Mara"}, workspace_id=WS)
    )
    repository.add_node(
        Node(id="loc_harbor", type="Location", properties={"name": "Harbor"}, workspace_id=WS)
    )
    repository.add_edge(
        Edge(
            source="char_mara",
            target="loc_harbor",
            type="LOCATED_AT",
            properties={"since": 12},
            workspace_id=WS,
        )
    )

    edges = repository.get_edges("char_mara", workspace_id=WS)
    assert {(edge.source, edge.target, edge.type) for edge in edges} == {
        ("char_mara", "loc_harbor", "LOCATED_AT")
    }
    assert edges[0].properties["since"] == 12
    assert [edge.source for edge in repository.get_edges("loc_harbor", workspace_id=WS)] == [
        "char_mara"
    ]


def test_get_nodes_filters_and_search(repository: Neo4jGraphRepository) -> None:
    repository.add_node(
        Node(id="char_mara", type="Character", properties={"name": "Mara"}, workspace_id=WS)
    )
    repository.add_node(
        Node(id="char_ivo", type="Character", properties={"name": "Ivo"}, workspace_id=WS)
    )
    repository.add_node(
        Node(id="loc_harbor", type="Location", properties={"name": "Harbor"}, workspace_id=WS)
    )

    characters = repository.get_nodes(workspace_id=WS, node_type="Character")
    assert {node.id for node in characters} == {"char_mara", "char_ivo"}
    assert [node.id for node in repository.get_nodes(workspace_id=WS, node_id="loc_harbor")] == [
        "loc_harbor"
    ]

    matches = repository.search_nodes_by_name("mar", workspace_id=WS)
    assert [match["id"] for match in matches] == ["char_mara"]


def test_delete_node_removes_attached_edges(repository: Neo4jGraphRepository) -> None:
    repository.add_node(
        Node(id="char_mara", type="Character", properties={"name": "Mara"}, workspace_id=WS)
    )
    repository.add_node(
        Node(id="loc_harbor", type="Location", properties={"name": "Harbor"}, workspace_id=WS)
    )
    repository.add_edge(
        Edge(source="char_mara", target="loc_harbor", type="LOCATED_AT", workspace_id=WS)
    )

    assert repository.delete_node("char_mara", workspace_id=WS) is True
    assert repository.get_node("char_mara", workspace_id=WS) is None
    assert repository.get_edges("loc_harbor", workspace_id=WS) == []
    assert repository.delete_node("char_mara", workspace_id=WS) is False


def test_ping(repository: Neo4jGraphRepository) -> None:
    assert repository.ping() is True


def test_created_at_is_persisted_and_stable(repository: Neo4jGraphRepository) -> None:
    from datetime import datetime

    created = datetime(2024, 1, 1, 12, 0, 0)
    repository.add_node(
        Node(
            id="char_mara",
            type="Character",
            properties={"name": "Mara"},
            workspace_id=WS,
            created_at=created,
        )
    )

    first = repository.get_node("char_mara", workspace_id=WS)
    second = repository.get_node("char_mara", workspace_id=WS)
    assert first is not None and second is not None
    assert first.created_at == created
    assert second.created_at == created
    assert "created_at" not in first.properties

    # Re-adding (MERGE) must not overwrite the original timestamp.
    repository.add_node(
        Node(
            id="char_mara",
            type="Character",
            properties={"age": 34},
            workspace_id=WS,
            created_at=datetime(2030, 1, 1),
        )
    )
    third = repository.get_node("char_mara", workspace_id=WS)
    assert third is not None
    assert third.created_at == created
    assert third.properties["age"] == 34


def test_workspace_isolation_and_cross_workspace_edge_refusal(
    repository: Neo4jGraphRepository,
) -> None:
    repository.add_node(
        Node(id="char_same", type="Character", properties={"name": "A"}, workspace_id=WS)
    )
    # A node in another workspace is invisible to this workspace's reads.
    assert repository.get_node("char_same", workspace_id=OTHER) is None

    repository.add_node(
        Node(id="loc_a", type="Location", properties={"name": "A"}, workspace_id=WS)
    )
    repository.add_node(
        Node(id="loc_b", type="Location", properties={"name": "B"}, workspace_id=OTHER)
    )
    # An edge cannot be created across workspaces: the target is not visible.
    repository.add_edge(
        Edge(source="char_same", target="loc_b", type="LOCATED_AT", workspace_id=WS)
    )
    assert repository.get_edges("char_same", workspace_id=WS) == []

    repository.add_edge(
        Edge(source="char_same", target="loc_a", type="LOCATED_AT", workspace_id=WS)
    )
    assert len(repository.get_edges("char_same", workspace_id=WS)) == 1
