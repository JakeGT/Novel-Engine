"""Records of the scene studio (ADR-022)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_VARIANTS = 3
MIN_WORDS = 150
MAX_WORDS = 3000
MAX_DIRECTION = 2000
MAX_VOICE = 4000
DRAFT_JOB = "scene_draft"
REVIEW_JOB = "scene_review"


@dataclass(frozen=True)
class SceneDraft:
    """A draft from the assistant, waiting for the author to keep or discard it."""

    workspace_id: str
    project_id: str
    id: str
    branch_id: str | None
    plan_id: str
    job_id: str
    manifest_id: str | None
    number: int
    content: str
    model: str
    status: str
    kept_version_id: str | None
    created_by: str | None
    created_at: datetime


@dataclass(frozen=True)
class AssistantFinding:
    """Something a check with the assistant found, quoting the text it is about."""

    id: str
    target_kind: str
    target_id: str
    job_id: str | None
    category: str
    message: str
    suggestion: str
    start: int | None
    end: int | None
    created_at: datetime | None = None


__all__ = [
    "DRAFT_JOB",
    "MAX_DIRECTION",
    "MAX_VARIANTS",
    "MAX_VOICE",
    "MAX_WORDS",
    "MIN_WORDS",
    "REVIEW_JOB",
    "AssistantFinding",
    "SceneDraft",
]
