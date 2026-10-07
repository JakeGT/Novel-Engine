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
