"""Immutable job envelope and idempotency contract.

No worker exists in Phase 1, but any job or idempotency contract introduced now
must carry the authenticated actor, the immutable workspace/project scope, an
authorization snapshot, and a correlation id. A future worker must re-authorize
the persisted scope before executing and must never trust project ids embedded
only inside an arbitrary payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from backend.domain.identity import AccessScope, AuthMode, utcnow

# Bump when the authorization policy changes so that in-flight jobs can be
# invalidated and re-derived.
AUTHORIZATION_POLICY_VERSION = "1"


@dataclass(frozen=True)
class AuthorizationSnapshot:
    """The authorization inputs captured when the job was accepted."""

    policy_version: str
    actor_user_id: str
    role: str


class JobAuthorizationError(RuntimeError):
    """The job's persisted scope or authorization snapshot is no longer valid."""


@dataclass(frozen=True)
class JobEnvelope:
    """Immutable scope + idempotency envelope for a background job."""

    job_id: str
    kind: str
    actor_user_id: str
    workspace_id: str
    project_id: str
    auth_mode: AuthMode
    authorization: AuthorizationSnapshot
    correlation_id: str
    idempotency_key: str
    created_at: datetime = field(default_factory=utcnow)
    payload: dict = field(default_factory=dict)

    def scope(self) -> AccessScope:
        """Rebuild the immutable scope from persisted fields (never from payload)."""
        return AccessScope(
            actor_user_id=self.actor_user_id,
            auth_mode=self.auth_mode,
            correlation_id=self.correlation_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
        )

    def revalidate(
        self,
        *,
        current_policy_version: str = AUTHORIZATION_POLICY_VERSION,
    ) -> None:
        """Workers call this before acting: scope is present and policy is current."""
        if not self.workspace_id or not self.project_id:
            raise JobAuthorizationError("job envelope is missing workspace/project scope")
        if self.authorization.policy_version != current_policy_version:
            raise JobAuthorizationError(
                "job authorization policy is stale; re-authorize before executing"
            )
        if self.authorization.actor_user_id != self.actor_user_id:
            raise JobAuthorizationError("job authorization actor does not match the envelope")


# ------------------------------------------------------------------ stored jobs (ADR-022)

# How long a claimed job is held before another worker may take it over. Extended at
# every step; a model call can take minutes.
LEASE_SECONDS = 600
# A job interrupted this many times is given up rather than tried again.
MAX_ATTEMPTS = 2
# While a model call is under way the lease is renewed this often, so a long call never
# lets the lease run out under a worker that is still working.
RENEW_SECONDS = LEASE_SECONDS // 4
# How many pieces of work one person may have waiting or under way in one project.
MAX_ACTIVE_JOBS = 3
# Passed as ``branch_id`` to a job listing to mean every line of the story.
ANY_BRANCH = "any"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class JobCancelled(RuntimeError):
    """The author asked for the job to stop; it stops at the next step."""


class LeaseLost(RuntimeError):
    """Another worker took the job over (or it was given up): this worker stops quietly
    and leaves the job's ending to whoever holds it now."""


@dataclass(frozen=True)
class Job:
    workspace_id: str
    project_id: str
    id: str
    kind: str
    status: JobStatus
    actor_user_id: str
    auth_mode: AuthMode
    policy_version: str
    role: str = ""
    stage: str = ""
    progress: int = 0
    payload: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    correlation_id: str = ""
    idempotency_key: str = ""
    cancel_requested: bool = False
    attempts: int = 0
    error_code: str | None = None
    error_message: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    def envelope(self) -> JobEnvelope:
        return JobEnvelope(
            job_id=self.id,
            kind=self.kind,
            actor_user_id=self.actor_user_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            auth_mode=self.auth_mode,
            authorization=AuthorizationSnapshot(
                policy_version=self.policy_version,
                actor_user_id=self.actor_user_id,
                role=self.role,
            ),
            correlation_id=self.correlation_id,
            idempotency_key=self.idempotency_key,
            created_at=self.created_at,
            payload=dict(self.payload),
        )
