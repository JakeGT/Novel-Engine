from __future__ import annotations

from pathlib import Path


class NullArtifactStore:
    """No-op artifact store; safe for read-only workflows and non-development envs."""

    def save(self, key: str, payload: str) -> None:
        return None

    def load(self, key: str) -> str | None:
        return None


class FilesystemArtifactStore:
    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)

    def save(self, key: str, payload: str) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        (self._directory / key).write_text(payload, encoding="utf-8")

    def load(self, key: str) -> str | None:
        path = self._directory / key
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
