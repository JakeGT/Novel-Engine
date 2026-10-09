"""Canon journal domain models: branches, reservations, mutation sets, envelopes, outbox.

These models are persistence-neutral. PostgreSQL is the authority for the commit
workflow (ADR-011); Neo4j holds accepted canon bounded by the finalized branch
head. No model depends on a driver row or an OIDC vendor SDK.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MutationStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    COMMITTING = "committing"
    COMMITTED = "committed"
    REJECTED = "rejected"
    RECOVERY_REQUIRED = "recovery_required"


class AttemptStatus(StrEnum):
    PREPARED = "prepared"
    GRAPH_APPLYING = "graph_applying"
    GRAPH_APPLIED = "graph_applied"
    COMMITTED = "committed"
    RECOVERY_REQUIRED = "recovery_required"
    INTEGRITY_BLOCKED = "integrity_blocked"


class ReservationStatus(StrEnum):
    ACTIVE = "active"
    FINALIZED = "finalized"
    RELEASED = "released"


class Branch(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    name: str
    is_root: bool = True
    head_revision: int = 0
    created_at: datetime


class MutationSet(BaseModel):
    """An ordered set of proposed operations awaiting validation/commit."""

    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    schema_version_id: str | None = None
    status: MutationStatus = MutationStatus.DRAFT
    origin: str = "author"
    actor_user_id: str | None = None
    correlation_id: str = ""
    idempotency_key: str = ""
    expected_head: int = 0
    intent: str = "edit"
    created_at: datetime
    updated_at: datetime


class MutationOperation(BaseModel):
    """The current revision of one proposed operation in a mutation set."""

    model_config = {"extra": "forbid"}

    ordinal: int
    operation_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    revision: int = 1


class OperationRevision(BaseModel):
    """Immutable history entry for an operation; an edit appends a new revision."""

    model_config = {"extra": "forbid"}

    mutation_id: str
    ordinal: int
    revision: int
    operation_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor_user_id: str | None = None
    reason: str = ""
    created_at: datetime


class OperationDecision(BaseModel):
    """A reviewer's accept/reject decision bound to one operation revision."""

    model_config = {"extra": "forbid"}

    id: str
    mutation_id: str
    ordinal: int
    operation_revision: int
    decision: str
    actor_user_id: str | None = None
    note: str | None = None
    created_at: datetime


class MutationTransition(BaseModel):
    """Append-only record of one mutation-set state change."""

    model_config = {"extra": "forbid"}

    id: str
    mutation_id: str
    from_status: MutationStatus
    to_status: MutationStatus
    actor_user_id: str | None = None
    reason: str
    correlation_id: str = ""
    idempotency_key: str = ""
    created_at: datetime


class BranchReservation(BaseModel):
    """A single active optimistic claim on the next revision of a branch."""

    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    mutation_id: str
    expected_revision: int
    reserved_revision: int
    status: ReservationStatus = ReservationStatus.ACTIVE
    created_at: datetime


class ReviewDecision(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    mutation_id: str
    decision: str
    actor_user_id: str | None = None
    note: str | None = None
    created_at: datetime


class CommitEnvelope(BaseModel):
    """Immutable, canonically-serialized record of an accepted revision."""

    model_config = {"extra": "forbid"}

    commit_id: str
    mutation_id: str
    workspace_id: str
    project_id: str
    branch_id: str
    revision: int
    expected_head: int
    schema_version_id: str | None = None
    canonical: str
    checksum: str
    actor_user_id: str | None = None
    created_at: datetime


class CommitAttempt(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    commit_id: str
    status: AttemptStatus
    detail: str | None = None
    created_at: datetime


class OutboxMessage(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    topic: str
    workspace_id: str
    project_id: str
    branch_id: str
    revision: int
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    created_at: datetime
    published_at: datetime | None = None


class JournalError(RuntimeError):
    """Base class for canon journal workflow failures."""


class HeadConflict(JournalError):
    """The branch head does not match the expected revision, or a reservation is active."""


class RevisionConflict(JournalError):
    """The same commit id was reused with different content (checksum mismatch)."""


class MutationNotFound(JournalError):
    """The referenced mutation set does not exist in this project."""
