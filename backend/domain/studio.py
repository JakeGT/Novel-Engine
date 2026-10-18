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
READ_JOB = "scene_read"
# A deep audit of a part of the story, scene by scene (CON-011).
AUDIT_JOB = "story_audit"


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
    # The pass of a check that found it (CON-008); None for a note from the earlier
    # all-in-one check.
    check_pass: str | None = None


@dataclass(frozen=True)
class SceneReading:
    """One reading of a scene's text into proposed changes (Phase 6)."""

    workspace_id: str
    project_id: str
    branch_id: str
    id: str
    plan_id: str
    source_id: str
    version_id: str
    job_id: str | None
    passages: int
    proposals: tuple[str, ...]
    discarded: int
    created_by: str | None
    created_at: datetime
    # "partial" until every passage is read; a stopped reading stays partial.
    status: str = "complete"
    # The [start, end] spans of the version read so far.
    covered: tuple[tuple[int, int], ...] = ()
    base_reading_id: str | None = None


__all__ = [
    "AUDIT_JOB",
    "READ_JOB",
    "SceneReading",
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
