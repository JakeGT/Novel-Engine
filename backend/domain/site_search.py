"""The site's search-by-meaning setup, kept on a page by a platform administrator.

The setup names an embedding service (or "your own" OpenAI-compatible address), a
model and a size. Its key is sealed with the server's credential key and is never
returned: only its last four characters and when it was set. Changing the provider or
the address is as sensitive as the key, so a change needs a recent sign-in and a new
key.

Nothing here carries a secret: the key's plaintext exists only inside the store while
it is being sealed or opened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.domain.errors import ForbiddenError, ValidationError

# "Signed in recently" = within this many seconds, for changes to where a key is sent.
RECENT_SIGN_IN_SECONDS = 15 * 60


class SiteSearchRefused(ValidationError):
    """A setup change the provider's check refused, with a distinct error code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class SignInAgainError(ForbiddenError):
    """A change to where a key is sent needs a sign-in within the last few minutes."""

    code = "sign_in_again"


@dataclass(frozen=True)
class SiteSearchSetup:
    """The one row that says how the site makes its search vectors."""

    provider: str
    model: str
    dimension: int
    # Custom or upstream override only; None means the provider's own address.
    base_url: str | None
    version: int
    updated_by: str | None
    updated_at: datetime
    # Did the provider accept the key when it was saved (None = never checked)?
    verified: bool | None


@dataclass(frozen=True)
class SiteSearchSecret:
    """Metadata for the site's sealed key. Never the key itself."""

    id: str
    key_fingerprint: str
    key_last4: str
    key_version: int
    provider: str
    host: str
    created_by: str | None
    updated_at: datetime


__all__ = [
    "RECENT_SIGN_IN_SECONDS",
    "SignInAgainError",
    "SiteSearchRefused",
    "SiteSearchSecret",
    "SiteSearchSetup",
]
