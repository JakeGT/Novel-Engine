"""Media identity and explicit evidence links (Phase 2).

Phase 2 implements *identity, provenance, and explicit links only*: project-scoped
asset records, immutable asset versions, and reviewed link roles. Bytes live in the
scoped object store; PostgreSQL is the lifecycle authority. Upload, scanning,
transcoding, captioning, embedding, and provider delivery are Phase 4+ (ADR-010),
so no API here implies an asset was processed or made safe for hosted use.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

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

    @property
    def is_active(self) -> bool:
        return self.retired_revision is None


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
    "is_link_role",
]
