"""Archive import ledger models.

An import is recorded before its first durable write and is only ``completed``
after every commit has replayed and been verified. Until then the project is
visibly not a successful restore: it can be retried (replay is idempotent) or
removed. The ledger carries counts and an error class, never archive content.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ImportStatus(StrEnum):
    IMPORTING = "importing"
    COMPLETED = "completed"
    FAILED = "failed"
    REMOVED = "removed"


class ArchiveImport(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    archive_checksum: str
    format_version: str
    status: ImportStatus
    stage: str = "validated"
    commits_total: int = 0
    commits_applied: int = 0
    objects_imported: int = 0
    object_keys: list[str] = Field(default_factory=list)
    error: str | None = None
    actor_user_id: str | None = None
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @property
    def is_successful(self) -> bool:
        return self.status is ImportStatus.COMPLETED


class ArchiveImportError(RuntimeError):
    """An import ledger operation was not permitted in the import's current state."""


__all__ = ["ArchiveImport", "ArchiveImportError", "ImportStatus"]
