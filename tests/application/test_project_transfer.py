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


def test_export_carries_object_bytes_and_import_writes_them(tmp_path) -> None:
    import base64

    from backend.adapters.objects import FilesystemObjectStore

    source = FilesystemObjectStore(str(tmp_path / "src"))
    source.put(b"cover", workspace_id="ws-a", project_id="prj-1", content_type="image/png")
    archive = ProjectTransferService(_seeded_graph(), objects=source).export_project(
        "ws-a", "prj-1"
    )

    entry = archive["objects"][0]
    assert base64.b64decode(entry["data"]) == b"cover"

    target = FilesystemObjectStore(str(tmp_path / "dst"))
    result = ProjectTransferService(InMemoryGraphRepository(), objects=target).import_project(
        "ws-b", "prj-9", archive
    )

    assert result["objects"] == 1
    imported = target.list_objects(workspace_id="ws-b", project_id="prj-9")
    assert len(imported) == 1
    assert target.open(imported[0].key, workspace_id="ws-b", project_id="prj-9") == b"cover"


def test_import_rejects_object_checksum_mismatch(tmp_path) -> None:
    from backend.adapters.objects import FilesystemObjectStore
    from backend.services.project_transfer import _checksum

    source = FilesystemObjectStore(str(tmp_path / "s"))
    source.put(b"cover", workspace_id="ws-a", project_id="prj-1")
    archive = ProjectTransferService(_seeded_graph(), objects=source).export_project(
        "ws-a", "prj-1"
    )
    archive["objects"][0]["checksum"] = "0" * 64
    archive["checksum"] = _checksum(archive)

    target = FilesystemObjectStore(str(tmp_path / "d"))
    with pytest.raises(TransferError, match="does not match"):
        ProjectTransferService(InMemoryGraphRepository(), objects=target).import_project(
            "ws-b", "prj-9", archive
        )


def test_import_rejects_invalid_base64(tmp_path) -> None:
    from backend.adapters.objects import FilesystemObjectStore
    from backend.services.project_transfer import _checksum

    source = FilesystemObjectStore(str(tmp_path / "s"))
    source.put(b"cover", workspace_id="ws-a", project_id="prj-1")
    archive = ProjectTransferService(_seeded_graph(), objects=source).export_project(
        "ws-a", "prj-1"
    )
    archive["objects"][0]["data"] = "not valid base64!!!"
    archive["checksum"] = _checksum(archive)

    target = FilesystemObjectStore(str(tmp_path / "d"))
    with pytest.raises(TransferError, match="base64"):
        ProjectTransferService(InMemoryGraphRepository(), objects=target).import_project(
            "ws-b", "prj-9", archive
        )


def test_import_rejects_byte_length_mismatch(tmp_path) -> None:
    from backend.adapters.objects import FilesystemObjectStore
    from backend.services.project_transfer import _checksum

    source = FilesystemObjectStore(str(tmp_path / "s"))
    source.put(b"cover", workspace_id="ws-a", project_id="prj-1")
    archive = ProjectTransferService(_seeded_graph(), objects=source).export_project(
        "ws-a", "prj-1"
    )
    archive["objects"][0]["byte_length"] = 999
    archive["checksum"] = _checksum(archive)

    target = FilesystemObjectStore(str(tmp_path / "d"))
    with pytest.raises(TransferError, match="byte_length"):
        ProjectTransferService(InMemoryGraphRepository(), objects=target).import_project(
            "ws-b", "prj-9", archive
        )


def test_import_preserves_node_created_at() -> None:
    from datetime import datetime

    created = datetime(2024, 1, 2, 3, 4, 5)
    graph = InMemoryGraphRepository()
    graph.add_node(
        Node(
            id="char_a",
            type="Character",
            properties={"name": "A"},
            workspace_id="ws-a",
            project_id="prj-1",
            created_at=created,
        )
    )
    archive = ProjectTransferService(graph).export_project("ws-a", "prj-1")

    target = InMemoryGraphRepository()
    ProjectTransferService(target).import_project("ws-b", "prj-9", archive)
    node = target.get_node("char_a", workspace_id="ws-b", project_id="prj-9")

    assert node is not None
    assert node.created_at == created


def test_import_is_idempotent_for_objects(tmp_path) -> None:
    from backend.adapters.objects import FilesystemObjectStore

    source = FilesystemObjectStore(str(tmp_path / "s"))
    source.put(b"cover", workspace_id="ws-a", project_id="prj-1")
    archive = ProjectTransferService(_seeded_graph(), objects=source).export_project(
        "ws-a", "prj-1"
    )

    target = FilesystemObjectStore(str(tmp_path / "d"))
    service = ProjectTransferService(InMemoryGraphRepository(), objects=target)
    first = service.import_project("ws-b", "prj-9", archive)
    second = service.import_project("ws-b", "prj-9", archive)

    assert first["objects"] == 1
    assert second["objects"] == 0
    assert len(target.list_objects(workspace_id="ws-b", project_id="prj-9")) == 1


def test_export_import_round_trip_preserves_graph_content() -> None:
    archive = ProjectTransferService(_seeded_graph()).export_project("ws-a", "prj-1")

    target = InMemoryGraphRepository()
    ProjectTransferService(target).import_project("ws-b", "prj-9", archive)
    reexported = ProjectTransferService(target).export_project("ws-b", "prj-9")

    assert reexported["nodes"] == archive["nodes"]
    assert reexported["edges"] == archive["edges"]
