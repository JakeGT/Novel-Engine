import pytest

from backend.adapters.objects import FilesystemObjectStore, NullObjectStore
from backend.domain.objects import ObjectNotFound, ObjectStoreError

pytestmark = pytest.mark.unit

WS = "ws-1"
PROJECT = "prj-1"
OTHER_PROJECT = "prj-2"


@pytest.fixture
def store(tmp_path) -> FilesystemObjectStore:
    return FilesystemObjectStore(str(tmp_path / "objects"))


def test_put_stat_open_verify_delete(store: FilesystemObjectStore) -> None:
    payload = b"an uploaded manuscript image"
    stat = store.put(payload, workspace_id=WS, project_id=PROJECT, content_type="image/png")

    # Server-generated opaque key; callers never choose a path.
    assert stat.key
    assert "/" not in stat.key and ".." not in stat.key
    assert stat.byte_length == len(payload)
    assert stat.content_type == "image/png"

    fetched = store.stat(stat.key, workspace_id=WS, project_id=PROJECT)
    assert fetched == stat
    assert store.open(stat.key, workspace_id=WS, project_id=PROJECT) == payload
    assert store.verify_checksum(stat.key, workspace_id=WS, project_id=PROJECT) is True

    assert store.delete(stat.key, workspace_id=WS, project_id=PROJECT) is True
    assert store.stat(stat.key, workspace_id=WS, project_id=PROJECT) is None
    assert store.delete(stat.key, workspace_id=WS, project_id=PROJECT) is False


def test_objects_are_isolated_by_project(store: FilesystemObjectStore) -> None:
    stat = store.put(b"project one bytes", workspace_id=WS, project_id=PROJECT)

    # The same scope in another project cannot see or read the object.
    assert store.stat(stat.key, workspace_id=WS, project_id=OTHER_PROJECT) is None
    with pytest.raises(ObjectNotFound):
        store.open(stat.key, workspace_id=WS, project_id=OTHER_PROJECT)
    assert store.delete(stat.key, workspace_id=WS, project_id=OTHER_PROJECT) is False
    # The original remains.
    assert store.verify_checksum(stat.key, workspace_id=WS, project_id=PROJECT) is True


def test_unsafe_scope_components_are_rejected(store: FilesystemObjectStore) -> None:
    with pytest.raises(ValueError):
        store.put(b"x", workspace_id="../escape", project_id=PROJECT)
    with pytest.raises(ValueError):
        store.open("key", workspace_id=WS, project_id="..")


def test_verify_checksum_detects_tampering(store: FilesystemObjectStore, tmp_path) -> None:
    stat = store.put(b"original", workspace_id=WS, project_id=PROJECT)
    data_path = store._paths(WS, PROJECT, stat.key)[0]
    data_path.write_bytes(b"tampered")

    assert store.verify_checksum(stat.key, workspace_id=WS, project_id=PROJECT) is False


def test_null_object_store_is_disabled() -> None:
    store = NullObjectStore()
    with pytest.raises(ObjectStoreError):
        store.put(b"x", workspace_id=WS, project_id=PROJECT)
    assert store.stat("k", workspace_id=WS, project_id=PROJECT) is None
