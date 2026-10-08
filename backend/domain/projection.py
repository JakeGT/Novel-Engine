"""Projection watermarks and canonical record-version history (ADR-012).

Neo4j (and later vector/search indexes) are rebuildable projections of PostgreSQL
canon. A watermark records how far a projection has consumed committed
envelopes so graph-backed responses can prove freshness or report lag.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ProjectionStatus(StrEnum):
    CURRENT = "current"
    LAGGING = "lagging"
    ERROR = "error"
    REBUILDING = "rebuilding"


class ProjectionWatermark(BaseModel):
    model_config = {"extra": "forbid"}

    projection: str
    workspace_id: str
    project_id: str
    branch_id: str
    revision: int = 0
    commit_id: str | None = None
    envelope_checksum: str | None = None
    projection_schema_version: str = "1"
    status: ProjectionStatus = ProjectionStatus.CURRENT
    last_error: str | None = None
    updated_at: datetime


class RecordVersion(BaseModel):
    """One applied operation against one canonical record (history/dependency index)."""

    model_config = {"extra": "forbid"}

    workspace_id: str
    project_id: str
    branch_id: str
    record_type: str
    record_id: str
    revision: int
    operation_type: str
    commit_id: str
    change: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CanonStorageError(RuntimeError):
    """Raised when canonical storage cannot apply or read committed state."""
