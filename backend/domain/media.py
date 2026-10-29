"""Media identity and explicit evidence links (Phase 2).

Phase 2 implements *identity, provenance, and explicit links only*: project-scoped
asset records, immutable asset versions, and reviewed link roles. Bytes live in the
scoped object store; PostgreSQL is the lifecycle authority. Upload, scanning,
transcoding, captioning, embedding, and provider delivery are Phase 4+ (ADR-010),
so no API here implies an asset was processed or made safe for hosted use.

Since batch 6 (AST-003, images only): each picture version has a processing state, the
rebuildable smaller copies a separate, limited process made of it, and its segments (the
whole picture and optional regions the author chose).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from backend.domain.errors import DomainError
from backend.domain.identity import utcnow


class MediaError(RuntimeError):
    """Base class for media identity/link failures."""


class AssetNotFound(MediaError):
    """The requested asset does not exist in the given scope."""


class AssetVersionNotFound(MediaError):
    """The requested asset version does not exist in the given scope."""


class AssetLinkError(MediaError):
    """A link could not be accepted or retired."""


class AssetLifecycle(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"
    QUARANTINED = "quarantined"
    DELETED = "deleted"


class LinkRole(StrEnum):
    DEPICTS = "DEPICTS"
    VOICE_OF = "VOICE_OF"
    SET_AT = "SET_AT"
    MAP_OF = "MAP_OF"
    EVIDENCE_FOR = "EVIDENCE_FOR"
    ATTACHED_TO = "ATTACHED_TO"


class MediaLifecycleAction(StrEnum):
    ASSET_REGISTERED = "asset_registered"
    ASSET_IMPORTED = "asset_imported"
    ASSET_RETIRED = "asset_retired"
    VERSION_REGISTERED = "version_registered"
    VERSION_IMPORTED = "version_imported"
    VERSION_RETIRED = "version_retired"
    # A picture's original file deleted before its story (AST-008): the version row stays
    # as the tombstone (checksum, size, type, when); the file and its copies are gone.
    ORIGINAL_DELETED = "original_deleted"
    ASSET_DELETED = "asset_deleted"
    # What a model said about a deleted picture (its observations and their search rows)
    # removed with it (AST-008); ``item_count`` says how many, never the words.
    NOTES_REMOVED = "notes_removed"


class MediaLifecycleEvent(BaseModel):
    """Append-only record of one asset or asset-version lifecycle change."""

    id: str
    workspace_id: str
    project_id: str
    asset_id: str
    asset_version_id: str | None = None
    action: MediaLifecycleAction
    actor_user_id: str | None = None
    canon_revision: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    # How many things the event removed (``notes_removed``); None for other events.
    item_count: int | None = None


class MediaAsset(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    name: str
    lifecycle: AssetLifecycle = AssetLifecycle.ACTIVE
    created_revision: int
    retired_revision: int | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def is_active(self) -> bool:
        return self.lifecycle == AssetLifecycle.ACTIVE


class MediaAssetVersion(BaseModel):
    """An immutable version of an asset. Bytes are addressed by an opaque object key."""

    id: str
    asset_id: str
    workspace_id: str
    project_id: str
    version: int
    object_key: str
    content_type: str
    byte_length: int
    checksum: str
    source: str | None = None
    licence: str | None = None
    attribution: str | None = None
    authority: str | None = None
    visibility: str | None = None
    created_revision: int
    retired_revision: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    # When its file was deleted (AST-008); the row itself is kept as the tombstone.
    deleted_at: datetime | None = None

    model_config = {"frozen": True}


class AssetLink(BaseModel):
    """An accepted, revisioned link between an asset (version) and a canon record."""

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    asset_id: str
    asset_version_id: str | None = None
    role: LinkRole = LinkRole.EVIDENCE_FOR
    target_record_type: str = "entity"
    target_record_id: str
    provenance_id: str | None = None
    created_revision: int
    retired_revision: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    # AST-004: "open" or "secret". A secret link reaches a point of view only when that
    # character is among ``known_by``; it never reaches a scene with no point of view.
    visibility: str = "open"
    known_by: list[str] = Field(default_factory=list)
    # A story time (a ``WorldTime`` as JSON) from which the link applies; None is always.
    applies_from: dict[str, Any] | None = None
    # AST-001: a story time from which it no longer applies; None is to the end.
    applies_until: dict[str, Any] | None = None
    # AST-001: True when the link shows the version it names (``asset_version_id``);
    # otherwise it shows the picture's newest version.
    shows_version: bool = False

    @property
    def is_active(self) -> bool:
        return self.retired_revision is None

    @property
    def is_secret(self) -> bool:
        return self.visibility == "secret"

    @property
    def is_timed(self) -> bool:
        return self.applies_from is not None or self.applies_until is not None


class UploadState(StrEnum):
    """Where a two-step picture upload is (AST-002)."""

    # Asked for, with its size and digest; the picture has not arrived yet.
    WAITING = "waiting"
    # Arrived whole and as promised; kept aside until it is finished.
    RECEIVED = "received"
    # Being made into a picture now.
    FINISHING = "finishing"
    FINISHED = "finished"
    # Not finished in time: what arrived was removed.
    EXPIRED = "expired"


class UploadTarget(StrEnum):
    ENTITY = "entity"
    SCENE = "plan_item"
    # A new version of a picture already in the story.
    ASSET = "asset"


class PictureUpload(BaseModel):
    """A two-step picture upload (AST-002): what was promised, then what arrived, then
    what finishing it made. Never holds the picture's bytes."""

    workspace_id: str
    project_id: str
    id: str
    branch_id: str | None = None
    actor_user_id: str | None = None
    target_type: UploadTarget
    target_id: str
    name: str | None = None
    expected_bytes: int
    expected_sha256: str
    state: UploadState = UploadState.WAITING
    staged_key: str | None = None
    content_type: str | None = None
    asset_id: str | None = None
    version_id: str | None = None
    link_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    finished_at: datetime | None = None

    model_config = {"frozen": True}


class ProcessingStatus(StrEnum):
    """Where a picture version is in processing (AST-003)."""

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class VariantKind(StrEnum):
    """A rebuildable smaller copy of a picture; never exported (ADR-010 amendment)."""

    THUMBNAIL = "thumbnail"
    WEB = "web"


class SegmentKind(StrEnum):
    WHOLE = "whole"
    REGION = "region"


# Why processing failed, in a fixed vocabulary (never a decoder's message), and what the
# author is told. ``retryable`` failures may succeed if tried again unchanged.
PROCESSING_FAILURES: dict[str, tuple[str, bool]] = {
    "too_large": ("This picture is larger than pictures may be.", False),
    "unsupported_type": ("This file is not a PNG, JPEG or WebP picture.", False),
    "too_many_pixels": (
        "This picture has too many pixels to open safely. Save a smaller copy and add that.",
        False,
    ),
    "damaged": (
        "This picture could not be read; the file may be damaged or cut short. "
        "Save it again and add it once more.",
        False,
    ),
    "took_too_long": ("Opening this picture took too long. Try again later.", True),
    "out_of_memory": ("This picture needed too much memory to open. Try a smaller copy.", False),
    "missing_original": ("The stored picture is missing, so no smaller copy could be made.", False),
    "processor_unavailable": (
        "Pictures cannot be prepared on this installation right now. Try again later.",
        True,
    ),
    "processor_failed": ("Something went wrong preparing this picture. Try again.", True),
}


def processing_failure_words(code: str | None) -> str | None:
    if code is None:
        return None
    return PROCESSING_FAILURES.get(code, PROCESSING_FAILURES["processor_failed"])[0]


class MediaProcessing(BaseModel):
    """Processing state of one asset version, keyed by its checksum and the processor."""

    workspace_id: str
    project_id: str
    asset_version_id: str
    asset_id: str
    status: ProcessingStatus
    processor_version: str
    source_checksum: str
    attempts: int = 0
    failure_code: str | None = None
    width: int | None = None
    height: int | None = None
    detected_format: str | None = None
    job_id: str | None = None
    queued_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)

    def is_current(self, *, checksum: str, processor_version: str) -> bool:
        return self.source_checksum == checksum and self.processor_version == processor_version


class MediaVariant(BaseModel):
    """A smaller copy of one asset version, made by a named processor version."""

    workspace_id: str
    project_id: str
    asset_version_id: str
    kind: VariantKind
    object_key: str
    content_type: str
    width: int
    height: int
    byte_length: int
    checksum: str
    source_checksum: str
    processor_version: str
    created_at: datetime = Field(default_factory=utcnow)


class MediaSegment(BaseModel):
    """A retrievable portion of a picture: the whole of it, or a region the author chose.

    Coordinates are in pixels of the picture as it is shown (orientation applied)."""

    workspace_id: str
    project_id: str
    id: str
    asset_id: str
    asset_version_id: str
    kind: SegmentKind
    x: int
    y: int
    width: int
    height: int
    label: str | None = None
    # The entry this part shows, when the author said (a region only; AST-007). The
    # author's own pointer, like the label: it states nothing about the story.
    entity_id: str | None = None
    content_hash: str
    created_by: str | None = None
    processor_version: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    retired_at: datetime | None = None


class StorageQuotaExceeded(DomainError):
    """The workspace has no room left for this picture (OPS-007)."""

    status_code = 413
    code = "storage_full"
    title = "No room left"


def link_applies_at(link: AssetLink, moment: Any, when_of: Any) -> Any:
    """Whether a picture link applies at a moment of story time (AST-001).

    ``None`` when the link holds throughout (no time, or only times not placed); else a
    ``Truth``: ``maybe`` when the scene has no time or the window cannot settle it.
    ``when_of`` looks up an event's time for a relative one. The window is ``[from,
    until)``: it applies from its start and no longer at its end."""
    from backend.domain.worldtime import Truth, is_unknown, parse_world_time, resolve, span_of
    from backend.domain.worldtime import true_at as _true_at

    # Read leniently: a link-like record from before AST-001 has no ``applies_until``.
    raw_start = getattr(link, "applies_from", None)
    raw_end = getattr(link, "applies_until", None)
    start = parse_world_time(raw_start) if raw_start is not None else None
    end = parse_world_time(raw_end) if raw_end is not None else None
    start = None if is_unknown(start) else start
    end = None if is_unknown(end) else end
    if start is None and end is None:
        return None
    if moment is None:
        return Truth.MAYBE
    span = span_of(
        resolve(start, when_of) if start is not None else None,
        resolve(end, when_of) if end is not None else None,
    )
    if not span.stated:
        return Truth.MAYBE
    return _true_at(span, moment)


def link_window_words(
    link: AssetLink, *, unit: str = "year", event_name: Any = lambda _id: None
) -> str | None:
    """The story time a link applies for, in words; None when it holds throughout."""
    from backend.domain.worldtime import describe, is_unknown, parse_world_time

    # Read leniently: a link-like record from before AST-001 has no ``applies_until``.
    raw_start = getattr(link, "applies_from", None)
    raw_end = getattr(link, "applies_until", None)
    start = parse_world_time(raw_start) if raw_start is not None else None
    end = parse_world_time(raw_end) if raw_end is not None else None
    start_words = (
        describe(start, unit=unit, event_name=event_name)
        if start is not None and not (is_unknown(start) and not start.label)
        else None
    )
    end_words = (
        describe(end, unit=unit, event_name=event_name)
        if end is not None and not (is_unknown(end) and not end.label)
        else None
    )
    if start_words and end_words:
        return f"from {start_words} until {end_words}"
    if start_words:
        return f"from {start_words} on"
    if end_words:
        return f"until {end_words}"
    return None


def is_link_role(value: str) -> bool:
    try:
        LinkRole(value)
    except ValueError:
        return False
    return True


__all__ = [
    "AssetLifecycle",
    "AssetLink",
    "AssetLinkError",
    "AssetNotFound",
    "AssetVersionNotFound",
    "LinkRole",
    "MediaAsset",
    "MediaAssetVersion",
    "MediaError",
    "MediaProcessing",
    "MediaSegment",
    "MediaVariant",
    "PROCESSING_FAILURES",
    "PictureUpload",
    "UploadState",
    "UploadTarget",
    "ProcessingStatus",
    "SegmentKind",
    "StorageQuotaExceeded",
    "VariantKind",
    "is_link_role",
    "link_applies_at",
    "link_window_words",
    "processing_failure_words",
]
