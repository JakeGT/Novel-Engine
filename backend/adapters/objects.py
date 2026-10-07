from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from backend.adapters.system import uuid7
from backend.domain.identity import utcnow
from backend.domain.objects import ObjectNotFound, ObjectStat, ObjectStoreError

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_component(value: str) -> str:
    if not value or value in {".", ".."} or not _SAFE_COMPONENT.match(value):
        raise ValueError(f"unsafe object path component: {value!r}")
    return value


def _rmtree_count(path: Path) -> int:
    if not path.exists():
        return 0
    removed = 0
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
            removed += 1
        elif child.is_dir():
            child.rmdir()
    path.rmdir()
    return removed


class FilesystemObjectStore:
    """Local immutable object store with server-generated opaque keys.

    Layout: ``<root>/<workspace_id>/<project_id>/<key>`` with a sidecar
    ``<key>.meta.json`` holding content type, byte length, and checksum. Full
    metadata databases and object de-duplication are intentionally out of scope.
    """

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _dir(self, workspace_id: str, project_id: str) -> Path:
        return self._root / _safe_component(workspace_id) / _safe_component(project_id)

    def _paths(self, workspace_id: str, project_id: str, key: str) -> tuple[Path, Path]:
        base = self._dir(workspace_id, project_id)
        safe_key = _safe_component(key)
        return base / safe_key, base / f"{safe_key}.meta.json"

    def put(
        self,
        data: bytes,
        *,
        workspace_id: str,
        project_id: str,
        content_type: str = "application/octet-stream",
    ) -> ObjectStat:
        key = str(uuid7())
        data_path, meta_path = self._paths(workspace_id, project_id, key)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        checksum = hashlib.sha256(data).hexdigest()
        stat = ObjectStat(
            key=key,
            workspace_id=workspace_id,
            project_id=project_id,
            content_type=content_type,
            byte_length=len(data),
            checksum=checksum,
            created_at=utcnow(),
        )
        meta_path.write_text(
            json.dumps(
                {
                    "key": stat.key,
                    "workspace_id": stat.workspace_id,
                    "project_id": stat.project_id,
                    "content_type": stat.content_type,
                    "byte_length": stat.byte_length,
                    "checksum": stat.checksum,
                    "created_at": stat.created_at.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        data_path.write_bytes(data)
        return stat

    def open(self, key: str, *, workspace_id: str, project_id: str) -> bytes:
        data_path, _meta = self._paths(workspace_id, project_id, key)
        if not data_path.is_file():
            raise ObjectNotFound(f"object {key!r} not found")
        return data_path.read_bytes()

    def stat(self, key: str, *, workspace_id: str, project_id: str) -> ObjectStat | None:
        _data_path, meta_path = self._paths(workspace_id, project_id, key)
        if not meta_path.is_file():
            return None
        record = json.loads(meta_path.read_text(encoding="utf-8"))
        return ObjectStat(
            key=record["key"],
            workspace_id=record["workspace_id"],
            project_id=record["project_id"],
            content_type=record["content_type"],
            byte_length=record["byte_length"],
            checksum=record["checksum"],
            created_at=datetime.fromisoformat(record["created_at"]),
        )

    def delete(self, key: str, *, workspace_id: str, project_id: str) -> bool:
        data_path, meta_path = self._paths(workspace_id, project_id, key)
        existed = data_path.is_file() or meta_path.is_file()
        data_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return existed

    def verify_checksum(self, key: str, *, workspace_id: str, project_id: str) -> bool:
        stat = self.stat(key, workspace_id=workspace_id, project_id=project_id)
        if stat is None:
            return False
        try:
            data = self.open(key, workspace_id=workspace_id, project_id=project_id)
        except ObjectNotFound:
            return False
        return hashlib.sha256(data).hexdigest() == stat.checksum

    def delete_workspace(self, *, workspace_id: str) -> int:
        return _rmtree_count(self._root / _safe_component(workspace_id))

    def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        return _rmtree_count(
            self._root / _safe_component(workspace_id) / _safe_component(project_id)
        )


class NullObjectStore:
    """No-op object store for environments without product media."""

    def put(
        self,
        data: bytes,
        *,
        workspace_id: str,
        project_id: str,
        content_type: str = "application/octet-stream",
    ) -> ObjectStat:
        raise ObjectStoreError("object storage is not configured")

    def open(self, key: str, *, workspace_id: str, project_id: str) -> bytes:
        raise ObjectNotFound(f"object {key!r} not found")

    def stat(self, key: str, *, workspace_id: str, project_id: str) -> ObjectStat | None:
        return None

    def delete(self, key: str, *, workspace_id: str, project_id: str) -> bool:
        return False

    def verify_checksum(self, key: str, *, workspace_id: str, project_id: str) -> bool:
        return False

    def delete_workspace(self, *, workspace_id: str) -> int:
        return 0

    def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        return 0
