from __future__ import annotations

import re
from pathlib import Path

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_component(value: str) -> str:
    if not value or value in {".", ".."} or not _SAFE_COMPONENT.match(value):
        raise ValueError(f"unsafe artifact path component: {value!r}")
    return value


class NullArtifactStore:
    """No-op debug artifact store; safe for read-only workflows and non-development envs."""

    def save(self, key: str, payload: str, *, workspace_id: str, project_id: str) -> None:
        return None

    def load(self, key: str, *, workspace_id: str, project_id: str) -> str | None:
        return None


class FilesystemArtifactStore:
    """Debug artifact store namespaced by workspace and project, with traversal guards."""

    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)

    def _path(self, key: str, workspace_id: str, project_id: str) -> Path:
        return (
            self._directory
            / _safe_component(workspace_id)
            / _safe_component(project_id)
            / _safe_component(key)
        )

    def save(self, key: str, payload: str, *, workspace_id: str, project_id: str) -> None:
        path = self._path(key, workspace_id, project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")

    def load(self, key: str, *, workspace_id: str, project_id: str) -> str | None:
        path = self._path(key, workspace_id, project_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
