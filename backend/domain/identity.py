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
    # The signed ID token as issued. Kept with the session only, so sign-out can
    # prove to the provider whose session is ending and be returned to the app.
    id_token: str | None = None
    # Seconds since the epoch when the provider actually authenticated the person
    # (the ID token's ``auth_time``); None when the provider did not say.
    auth_time: int | None = None


class Session(BaseModel):
    """A server-side application session. ``id`` is the raw bearer token; the store persists only a hash."""

    id: str
    user_id: str
    csrf_token: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    rotated_from: str | None = None
    # Hosted sessions only; presented to the identity provider at sign-out.
    id_token: str | None = None
    # When the person actually signed in; carried unchanged through session rotation,
    # so "signed in recently" keeps meaning the same thing however often the id turns.
    authenticated_at: datetime = Field(default_factory=utcnow)


class LoginAttempt(BaseModel):
    """Transient OIDC login state, consumed exactly once at the callback."""

    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    # Asked at /auth/login, stored here so the callback cannot add it.
    fresh: bool = False
    # A same-site path to return to after a successful login; None means "/".
    return_to: str | None = None


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


class StaleSignIn(IdentityError):
    """A fresh sign-in was asked for, but the provider did not just authenticate."""


class SessionInvalid(IdentityError):
    """The session is missing, expired, or revoked."""
