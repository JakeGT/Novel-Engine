from collections.abc import Iterator

import pytest

from backend.adapters.graph import Neo4jGraphRepository
from backend.graph.manager import GraphManager
from backend.graph.schema import Edge, Node

pytestmark = pytest.mark.integration

WS = "ws-test"
PROJECT = "prj-test"
OTHER_WS = "ws-other"
OTHER_PROJECT = "prj-other"


@pytest.fixture
def repository(neo4j_driver, neo4j_settings) -> Iterator[Neo4jGraphRepository]:
    manager = GraphManager(neo4j_settings)
    try:
        yield Neo4jGraphRepository(manager)
    finally:
        manager.close()


def _node(node_id: str, node_type: str, **properties: object) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        properties=dict(properties),
        workspace_id=WS,
        project_id=PROJECT,
    )


def test_node_roundtrip_preserves_type_and_properties(repository: Neo4jGraphRepository) -> None:
    repository.add_node(_node("char_mara", "Character", name="Mara Voss", age=34))

    node = repository.get_node("char_mara", workspace_id=WS, project_id=PROJECT)
    assert node is not None
    assert node.type == "Character"
    assert node.properties["name"] == "Mara Voss"
    assert node.properties["age"] == 34
    assert repository.get_node("missing", workspace_id=WS, project_id=PROJECT) is None


def test_update_node_merges_properties(repository: Neo4jGraphRepository) -> None:
    repository.add_node(_node("loc_harbor", "Location", name="Harbor"))
    updated = repository.update_node(_node("loc_harbor", "Location", population=900))

    assert updated is not None
    assert updated.type == "Location"
    assert updated.properties["name"] == "Harbor"
    assert updated.properties["population"] == 900


def test_edges_and_neighbors(repository: Neo4jGraphRepository) -> None:
    repository.add_node(_node("char_mara", "Character", name="Mara"))
    repository.add_node(_node("loc_harbor", "Location", name="Harbor"))
    repository.add_edge(
        Edge(
            source="char_mara",
            target="loc_harbor",
            type="LOCATED_AT",
            properties={"since": 12},
            workspace_id=WS,
            project_id=PROJECT,
        )
    )

    edges = repository.get_edges("char_mara", workspace_id=WS, project_id=PROJECT)
    assert {(edge.source, edge.target, edge.type) for edge in edges} == {
        ("char_mara", "loc_harbor", "LOCATED_AT")
    }
    assert edges[0].properties["since"] == 12
    assert [
        edge.source
        for edge in repository.get_edges("loc_harbor", workspace_id=WS, project_id=PROJECT)
    ] == ["char_mara"]


def test_get_nodes_filters_and_search(repository: Neo4jGraphRepository) -> None:
    repository.add_node(_node("char_mara", "Character", name="Mara"))
    repository.add_node(_node("char_ivo", "Character", name="Ivo"))
    repository.add_node(_node("loc_harbor", "Location", name="Harbor"))

    characters = repository.get_nodes(workspace_id=WS, project_id=PROJECT, node_type="Character")
    assert {node.id for node in characters} == {"char_mara", "char_ivo"}
    assert [
        node.id
        for node in repository.get_nodes(workspace_id=WS, project_id=PROJECT, node_id="loc_harbor")
    ] == ["loc_harbor"]

    matches = repository.search_nodes_by_name("mar", workspace_id=WS, project_id=PROJECT)
    assert [match["id"] for match in matches] == ["char_mara"]


def test_semantic_search_scores_only_the_requested_project(
    repository: Neo4jGraphRepository,
) -> None:
    target = [1.0] + [0.0] * 383
    repository.add_node(
        _node("local_exact", "Character", name="Local").model_copy(update={"embedding": target})
    )
    # A closer-looking colliding id and many unrelated-project vectors must never
    # affect the scoped candidate set or score.
    for index in range(25):
        repository.add_node(
            Node(
                id="local_exact" if index == 0 else f"foreign_{index}",
                type="Character",
                properties={"name": f"Foreign {index}"},
                embedding=target,
                workspace_id=OTHER_WS,
                project_id=OTHER_PROJECT,
            )
        )

    assert repository.search_semantic_nodes(
        target, workspace_id=WS, project_id=PROJECT, top_k=5, min_score=0.99
    ) == ["local_exact"]


def test_delete_node_removes_attached_edges(repository: Neo4jGraphRepository) -> None:
    repository.add_node(_node("char_mara", "Character", name="Mara"))
    repository.add_node(_node("loc_harbor", "Location", name="Harbor"))
    repository.add_edge(
        Edge(
            source="char_mara",
            target="loc_harbor",
            type="LOCATED_AT",
            workspace_id=WS,
            project_id=PROJECT,
        )
    )

    assert repository.delete_node("char_mara", workspace_id=WS, project_id=PROJECT) is True
    assert repository.get_node("char_mara", workspace_id=WS, project_id=PROJECT) is None
    assert repository.get_edges("loc_harbor", workspace_id=WS, project_id=PROJECT) == []
    assert repository.delete_node("char_mara", workspace_id=WS, project_id=PROJECT) is False


def test_ping(repository: Neo4jGraphRepository) -> None:
    assert repository.ping() is True


def test_created_at_is_persisted_and_stable(repository: Neo4jGraphRepository) -> None:
    from datetime import datetime

    created = datetime(2024, 1, 1, 12, 0, 0)
    node = _node("char_mara", "Character", name="Mara")
    repository.add_node(node.model_copy(update={"created_at": created}))

    first = repository.get_node("char_mara", workspace_id=WS, project_id=PROJECT)
    second = repository.get_node("char_mara", workspace_id=WS, project_id=PROJECT)
    assert first is not None and second is not None
    assert first.created_at == created
    assert second.created_at == created
    assert "created_at" not in first.properties

    # Re-adding (MERGE) must not overwrite the original timestamp.
    repository.add_node(
        _node("char_mara", "Character", age=34).model_copy(
            update={"created_at": datetime(2030, 1, 1)}
        )
    )
    third = repository.get_node("char_mara", workspace_id=WS, project_id=PROJECT)
    assert third is not None
    assert third.created_at == created
    assert third.properties["age"] == 34


def test_cross_workspace_isolation_and_edge_refusal(repository: Neo4jGraphRepository) -> None:
    repository.add_node(_node("char_same", "Character", name="A"))
    assert repository.get_node("char_same", workspace_id=OTHER_WS, project_id=PROJECT) is None

    repository.add_node(_node("loc_a", "Location", name="A"))
    repository.add_node(
        Node(
            id="loc_b",
            type="Location",
            properties={"name": "B"},
            workspace_id=OTHER_WS,
            project_id=PROJECT,
        )
    )
    repository.add_edge(
        Edge(
            source="char_same",
            target="loc_b",
            type="LOCATED_AT",
            workspace_id=WS,
            project_id=PROJECT,
        )
    )
    assert repository.get_edges("char_same", workspace_id=WS, project_id=PROJECT) == []

    repository.add_edge(
        Edge(
            source="char_same",
            target="loc_a",
            type="LOCATED_AT",
            workspace_id=WS,
            project_id=PROJECT,
        )
    )
    assert len(repository.get_edges("char_same", workspace_id=WS, project_id=PROJECT)) == 1


def test_cross_project_isolation_in_same_workspace(repository: Neo4jGraphRepository) -> None:
    repository.add_node(_node("char_same", "Character", name="A"))

    # Same workspace, different project: the node is invisible.
    assert repository.get_node("char_same", workspace_id=WS, project_id=OTHER_PROJECT) is None
    assert repository.get_nodes(workspace_id=WS, project_id=OTHER_PROJECT) == []
    assert repository.search_nodes_by_name("A", workspace_id=WS, project_id=OTHER_PROJECT) == []
    # It cannot be deleted through the other project either.
    assert repository.delete_node("char_same", workspace_id=WS, project_id=OTHER_PROJECT) is False

    # A cross-project edge is never created: the target is invisible in this project.
    repository.add_node(_node("loc_a", "Location", name="A"))
    repository.add_node(
        Node(
            id="loc_other",
            type="Location",
            properties={"name": "Other"},
            workspace_id=WS,
            project_id=OTHER_PROJECT,
        )
    )
    repository.add_edge(
        Edge(
            source="char_same",
            target="loc_other",
            type="LOCATED_AT",
            workspace_id=WS,
            project_id=PROJECT,
        )
    )
    assert repository.get_edges("char_same", workspace_id=WS, project_id=PROJECT) == []

    # Identity is scoped: the same id in another project is a distinct node and
    # must not mutate this project's node.
    repository.add_node(
        Node(
            id="char_same",
            type="Character",
            properties={"name": "B"},
            workspace_id=WS,
            project_id=OTHER_PROJECT,
        )
    )
    project_node = repository.get_node("char_same", workspace_id=WS, project_id=PROJECT)
    other_node = repository.get_node("char_same", workspace_id=WS, project_id=OTHER_PROJECT)
    assert project_node is not None and project_node.properties["name"] == "A"
    assert other_node is not None and other_node.properties["name"] == "B"
