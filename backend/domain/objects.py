"""Immutable, project-scoped object storage contracts.

Product objects (uploaded media, exports) are distinct from the debug text
artifact store. Object keys are server-generated and opaque; callers never choose
or see a filesystem path. Full upload/scanning/transcoding APIs are Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from backend.domain.identity import utcnow


@dataclass(frozen=True)
class ObjectStat:
    key: str
    workspace_id: str
    project_id: str
    content_type: str
    byte_length: int
    checksum: str
    created_at: datetime = field(default_factory=utcnow)


class ObjectStoreError(RuntimeError):
    """Base class for object-store failures."""


class ObjectNotFound(ObjectStoreError):
    """The requested object does not exist in the given scope."""
