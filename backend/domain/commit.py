"""Commit application markers and outcomes (ADR-011).

The graph writer records one immutable marker per commit id so re-applying the
same envelope is a no-op and a checksum disagreement is an integrity error.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ApplyOutcome(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"


class CommitMarker(BaseModel):
    model_config = {"extra": "forbid"}

    commit_id: str
    workspace_id: str
    project_id: str
    branch_id: str
    revision: int
    checksum: str
