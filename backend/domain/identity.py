"""Identity and access domain models.

These models are provider-neutral: no OIDC vendor SDK or database row leaks into
them. External identities are keyed by the immutable ``(issuer, subject)`` pair;
email and display name are mutable claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Role(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class AuthMode(StrEnum):
    LOCAL = "local"
    HOSTED = "hosted"


class User(BaseModel):
    id: str
    email: str | None = None
    display_name: str | None = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class ExternalIdentity(BaseModel):
    """A user's link to one provider identity, keyed by (issuer, subject)."""

    issuer: str
    subject: str
    user_id: str
    email: str | None = None
    display_name: str | None = None
    linked_at: datetime = Field(default_factory=utcnow)


class IdentityClaims(BaseModel):
    """Validated claims from an identity provider. ``raw`` is never persisted verbatim."""

    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    """A server-side application session. ``id`` is the raw bearer token; the store persists only a hash."""

    id: str
    user_id: str
    csrf_token: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    rotated_from: str | None = None


class LoginAttempt(BaseModel):
    """Transient OIDC login state, consumed exactly once at the callback."""

    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


@dataclass(frozen=True)
class AccessScope:
    """Request/job scope. Workspace/project are optional until tenancy lands (1B/1D)."""

    actor_user_id: str
    auth_mode: AuthMode
    correlation_id: str = ""
    workspace_id: str | None = None
    project_id: str | None = None


class IdentityError(RuntimeError):
    """Base class for identity/authentication failures."""


class IdentityProviderUnavailable(IdentityError):
    """The identity provider is not configured or unreachable."""


class InvalidIdentityToken(IdentityError):
    """The provided identity token failed validation."""


class SessionInvalid(IdentityError):
    """The session is missing, expired, or revoked."""
