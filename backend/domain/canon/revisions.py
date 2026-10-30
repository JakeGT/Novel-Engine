"""Immutable revisions and provenance.

Provenance explains why canonical state exists and from which evidence; it never
embeds raw prompts, credentials, or secret content. ``CanonRevision`` describes a
committed revision bound to a branch head.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class OriginKind(StrEnum):
    AUTHOR = "author"
    MODEL = "model"
    IMPORT = "import"
    DERIVED = "derived"
    MIGRATION = "migration"
    SYSTEM = "system"
    # Another assistant the author connected with an access key (QRY-003). Always reviewed.
    MACHINE = "machine"


class Provenance(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    origin: OriginKind
    actor_user_id: str | None = None
    job_id: str | None = None
    # Bounded reference to an evidence location or source artefact, never copied content.
    source_ref: str | None = None
    note: str | None = None
    created_at: datetime


class CanonRevision(BaseModel):
    model_config = {"extra": "forbid"}

    revision: int
    workspace_id: str
    project_id: str
    branch_id: str
    commit_id: str
    expected_head: int
    result_head: int
    schema_version_id: str | None = None
    envelope_checksum: str
    created_at: datetime
