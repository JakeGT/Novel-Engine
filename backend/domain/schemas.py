"""Immutable, project-scoped schema versions.

A schema version is the unit of governance: a project drafts a bounded declarative
definition, validates compatibility, then publishes an immutable version. Existing
canon retains the version under which it was accepted.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from backend.domain.identity import utcnow
from backend.domain.schema_defs import SchemaDefinition


class SchemaVersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class SchemaVersion(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    version: int
    status: SchemaVersionStatus
    definition: dict[str, Any]
    content_hash: str
    compatibility: str
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    published_at: datetime | None = None

    def parsed_definition(self) -> SchemaDefinition:
        return SchemaDefinition.model_validate(self.definition)


class SchemaGovernanceError(RuntimeError):
    """Base class for schema governance failures."""


class SchemaVersionNotFound(SchemaGovernanceError):
    """The requested schema version does not exist for this project."""


class SchemaCompatibilityError(SchemaGovernanceError):
    """The requested publish is forbidden relative to the published schema."""


class SchemaDraftError(SchemaGovernanceError):
    """The draft definition is invalid or cannot be stored."""
