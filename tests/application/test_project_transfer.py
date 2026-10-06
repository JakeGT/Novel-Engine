import pytest

from backend.graph.schema import Edge, Node
from backend.services.project_transfer import ProjectTransferService, TransferError
from tests.doubles import InMemoryGraphRepository

pytestmark = pytest.mark.application


def _seeded_graph() -> InMemoryGraphRepository:
    graph = InMemoryGraphRepository()
    graph.add_node(
        Node(id="char_a", type="Character", properties={"name": "A"}, workspace_id="ws-a")
    )
    graph.add_node(Node(id="loc_b", type="Location", properties={"name": "B"}, workspace_id="ws-a"))
    graph.add_edge(Edge(source="char_a", target="loc_b", type="LOCATED_AT", workspace_id="ws-a"))
    return graph


def test_export_import_round_trip_isolates_target_workspace() -> None:
    bundle = ProjectTransferService(_seeded_graph()).export_workspace("ws-a")
    assert bundle["checksum"]
    assert "credentials" not in bundle and "sessions" not in bundle

    target = InMemoryGraphRepository()
    report = ProjectTransferService(target).import_workspace("ws-b", bundle)

    assert report == {"nodes": 2, "edges": 1, "checksum": bundle["checksum"]}
    assert {node.id for node in target.get_nodes(workspace_id="ws-b")} == {"char_a", "loc_b"}
    assert target.get_node("char_a", workspace_id="ws-b") is not None
    # The source workspace is untouched and the target does not leak into it.
    assert target.get_node("char_a", workspace_id="ws-a") is None


def test_import_rejects_tampered_bundle() -> None:
    bundle = ProjectTransferService(_seeded_graph()).export_workspace("ws-a")
    bundle["nodes"][0]["properties"]["name"] = "Tampered"

    with pytest.raises(TransferError, match="checksum"):
        ProjectTransferService(InMemoryGraphRepository()).import_workspace("ws-b", bundle)
