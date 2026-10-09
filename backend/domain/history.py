"""Canon revision history, per-record provenance history, and named tags (2H).

These models are storage-neutral: they describe committed history and inspection
views over canonical state, never the transport or driver shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HistoryError(RuntimeError):
    """Raised when a history or inspection request cannot be satisfied."""


class CanonTagError(HistoryError):
    """Raised when a tag operation is invalid."""


class RevisionSummary(BaseModel):
    """A committed revision, without exposing raw operation payloads."""

    model_config = {"extra": "forbid"}

    revision: int
    commit_id: str
    expected_head: int
    result_head: int
    schema_version_id: str | None = None
    envelope_checksum: str
    operation_count: int = 0
    created_at: datetime


class RecordHistoryEntry(BaseModel):
    """One operation applied to one canonical record, mapped to its commit."""

    model_config = {"extra": "forbid"}

    record_type: str
    record_id: str
    branch_id: str
    revision: int
    operation_type: str
    commit_id: str
    change: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CanonTag(BaseModel):
    """An immutable named pointer to an exact branch revision."""

    model_config = {"extra": "forbid"}

    workspace_id: str
    project_id: str
    branch_id: str
    name: str
    revision: int
    commit_id: str
    created_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CanonTagHistoryEntry(BaseModel):
    model_config = {"extra": "forbid"}

    workspace_id: str
    project_id: str
    branch_id: str
    name: str
    revision: int
    commit_id: str
    replaced_at: datetime


class RevertChange(BaseModel):
    """One change in a reversibility preview; ``inverse`` is null when manual."""

    model_config = {"extra": "forbid"}

    revision: int
    record_type: str
    record_id: str
    operation_type: str
    inverse_operation: str | None = None
    reason: str = ""


class RevertProposal(BaseModel):
    """A proposed compensating mutation set; never rewrites committed envelopes."""

    model_config = {"extra": "forbid"}

    from_revision: int
    to_revision: int
    head_revision: int
    operations: list[dict[str, Any]] = Field(default_factory=list)
    changes: list[RevertChange] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    summary: str = ""
