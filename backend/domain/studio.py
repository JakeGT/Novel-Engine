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
class PictureAnchor:
    """The picture a note of the pictures check was compared with (CON-014): its exact
    version and, when the note named one, the part of it: a part the author marked
    (``segment_id``) or a rectangle (``region``: left, top, width, height as fractions
    of the picture). Shown drawn on the picture with the note."""

    asset_id: str
    version_id: str
    label: str = ""
    segment_id: str | None = None
    region: tuple[float, float, float, float] | None = None
    part_label: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "version_id": self.version_id,
            "label": self.label,
            "segment_id": self.segment_id,
            "region": list(self.region) if self.region is not None else None,
            "part_label": self.part_label,
        }

    @classmethod
    def from_dict(cls, raw: object) -> PictureAnchor | None:
        """An anchor as stored; None for anything malformed (never an error)."""
        if not isinstance(raw, dict):
            return None
        asset_id, version_id = raw.get("asset_id"), raw.get("version_id")
        if not isinstance(asset_id, str) or not isinstance(version_id, str):
            return None
        region = raw.get("region")
        box: tuple[float, float, float, float] | None = None
        if isinstance(region, list) and len(region) == 4:
            try:
                x, y, w, h = (float(value) for value in region)
            except (TypeError, ValueError):
                x = y = w = h = -1.0
            if 0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1:
                box = (x, y, min(w, 1 - x), min(h, 1 - y))
        segment = raw.get("segment_id")
        part = raw.get("part_label")
        return cls(
            asset_id=asset_id,
            version_id=version_id,
            label=str(raw.get("label") or "")[:200],
            segment_id=segment if isinstance(segment, str) else None,
            region=box,
            part_label=str(part)[:200] if isinstance(part, str) and part else None,
        )


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
    # A note of the pictures check: the picture, and the part of it, it is about (CON-014).
    picture_anchor: PictureAnchor | None = None


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
    # Thread steps found but not suggested yet (EXT-007): (thread, step, where, and for a
    # thread the scene opens, its suggested title and summary). Kept so a reading stopped
    # part way passes them to the next instead of losing them.
    steps: tuple[tuple[str, str, str, str, str], ...] = ()


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
