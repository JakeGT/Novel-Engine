import pytest

from backend.graph.schema import Edge, Node
from backend.services.project_transfer import ProjectTransferService, TransferError
from tests.doubles import InMemoryGraphRepository

pytestmark = pytest.mark.application


def _seeded_graph() -> InMemoryGraphRepository:
    graph = InMemoryGraphRepository()
    graph.add_node(
        Node(
            id="char_a",
            type="Character",
            properties={"name": "A"},
            workspace_id="ws-a",
            project_id="prj-1",
        )
    )
    graph.add_node(
        Node(
            id="loc_b",
            type="Location",
            properties={"name": "B"},
            workspace_id="ws-a",
            project_id="prj-1",
        )
    )
    graph.add_edge(
        Edge(
            source="char_a",
            target="loc_b",
            type="LOCATED_AT",
            workspace_id="ws-a",
            project_id="prj-1",
        )
    )
    # A sibling project in the same workspace must never be exported.
    graph.add_node(
        Node(
            id="char_other",
            type="Character",
            properties={"name": "Other"},
            workspace_id="ws-a",
            project_id="prj-2",
        )
    )
    return graph


def test_export_import_round_trip_isolates_target_project() -> None:
    archive = ProjectTransferService(_seeded_graph()).export_project("ws-a", "prj-1")
    assert archive["checksum"]
    assert archive["project"]["workspace_id"] == "ws-a"
    assert archive["project"]["project_id"] == "prj-1"
    assert archive["project"]["node_count"] == 2
    assert {node["id"] for node in archive["nodes"]} == {"char_a", "loc_b"}
    assert archive["objects"] == []
    assert "credentials" not in archive and "sessions" not in archive

    target = InMemoryGraphRepository()
    report = ProjectTransferService(target).import_project("ws-b", "prj-9", archive)

    assert report["nodes"] == 2
    assert report["edges"] == 1
    assert report["objects"] == 0
    assert {node.id for node in target.get_nodes(workspace_id="ws-b", project_id="prj-9")} == {
        "char_a",
        "loc_b",
    }
    assert target.get_node("char_a", workspace_id="ws-b", project_id="prj-9") is not None
    # The source workspace/project is untouched, and the target does not leak into it.
    assert target.get_node("char_a", workspace_id="ws-a", project_id="prj-1") is None


def test_export_excludes_sibling_project_in_same_workspace() -> None:
    archive = ProjectTransferService(_seeded_graph()).export_project("ws-a", "prj-1")
    assert "char_other" not in {node["id"] for node in archive["nodes"]}


def test_import_rejects_tampered_archive() -> None:
    archive = ProjectTransferService(_seeded_graph()).export_project("ws-a", "prj-1")
    archive["nodes"][0]["properties"]["name"] = "Tampered"

    with pytest.raises(TransferError, match="checksum"):
        ProjectTransferService(InMemoryGraphRepository()).import_project("ws-b", "prj-9", archive)


def test_import_rejects_unknown_archive_version() -> None:
    archive = ProjectTransferService(_seeded_graph()).export_project("ws-a", "prj-1")
    archive["archive_version"] = "999"

    with pytest.raises(TransferError, match="version"):
        ProjectTransferService(InMemoryGraphRepository()).import_project("ws-b", "prj-9", archive)


def test_import_preflight_rejects_edge_outside_archive() -> None:
    archive = ProjectTransferService(_seeded_graph()).export_project("ws-a", "prj-1")
    archive["edges"][0]["target"] = "ghost"
    from backend.services.project_transfer import _checksum

    archive["checksum"] = _checksum(archive)

    with pytest.raises(TransferError, match="outside the archive"):
        ProjectTransferService(InMemoryGraphRepository()).import_project("ws-b", "prj-9", archive)


def test_export_includes_object_manifest(tmp_path) -> None:
    from backend.adapters.objects import FilesystemObjectStore

    objects = FilesystemObjectStore(str(tmp_path))
    objects.put(b"cover", workspace_id="ws-a", project_id="prj-1", content_type="image/png")

    archive = ProjectTransferService(_seeded_graph(), objects=objects).export_project(
        "ws-a", "prj-1"
    )

    assert len(archive["objects"]) == 1
    entry = archive["objects"][0]
    assert entry["content_type"] == "image/png"
    assert entry["byte_length"] == 5
    assert entry["checksum"]


def test_import_preflight_rejects_object_without_checksum() -> None:
    archive = ProjectTransferService(_seeded_graph()).export_project("ws-a", "prj-1")
    archive["objects"] = [{"key": "x"}]
    from backend.services.project_transfer import _checksum

    archive["checksum"] = _checksum(archive)

    with pytest.raises(TransferError, match="checksum"):
        ProjectTransferService(InMemoryGraphRepository()).import_project("ws-b", "prj-9", archive)
