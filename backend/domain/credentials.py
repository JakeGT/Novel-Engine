"""Provider credential domain models.

Credentials are workspace-scoped secrets for a user's own provider account
(OpenRouter, Anthropic, OpenAI, Google, xAI, Mistral, DeepSeek). Plaintext key material never enters these models; only metadata and
a fingerprint are exposed.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from backend.domain.identity import utcnow


class CredentialProvider(StrEnum):
    # Each has an entry in ``backend.domain.providers.PROVIDERS``.
    OPENROUTER = "openrouter"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    XAI = "xai"
    MISTRAL = "mistral"
    DEEPSEEK = "deepseek"


class CredentialStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class Credential(BaseModel):
    """Provider credential metadata. Never carries the secret."""

    id: str
    workspace_id: str
    owner_user_id: str
    provider: CredentialProvider
    label: str
    key_fingerprint: str
    key_last4: str
    key_version: int
    status: CredentialStatus = CredentialStatus.ACTIVE
    created_at: datetime = Field(default_factory=utcnow)
    rotated_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    # Whether the provider accepted the key when it was last saved; None when it was
    # never checked (a key from before this was recorded, or read back from the vault).
    verified: bool | None = None


class CredentialError(RuntimeError):
    """Base class for credential-vault failures."""


class CredentialNotFound(CredentialError):
    """No such credential in this workspace."""


class CredentialUnavailable(CredentialError):
    """The vault is not configured (no KEK), so credentials cannot be stored."""
