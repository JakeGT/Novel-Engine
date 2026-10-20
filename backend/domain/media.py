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

    @property
    def is_active(self) -> bool:
        return self.retired_revision is None

    @property
    def is_secret(self) -> bool:
        return self.visibility == "secret"


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
    "ProcessingStatus",
    "SegmentKind",
    "StorageQuotaExceeded",
    "VariantKind",
    "is_link_role",
    "processing_failure_words",
]
