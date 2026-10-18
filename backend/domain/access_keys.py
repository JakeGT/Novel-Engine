"""Access keys for other assistants (QRY-003, ADR-024).

A member creates a personal access key so an assistant they already use can read
one story (or every story of theirs in one workspace) through the MCP endpoint.
The key is shown once; only its SHA-256 digest is stored, so it can never be shown
again. It is read-only unless the member also allowed it to suggest changes, and a
suggestion is only ever a proposal in the review queue.

Format (after GitHub's and GitLab's token formats): a fixed prefix that secret
scanners can match, 43 random base62 characters (256 bits), and a 6-character
base62 CRC32 of the random part, so a mistyped or made-up key is refused before
anything is looked up. The checksum is not a security control; the digest is.
"""

from __future__ import annotations

import secrets
import zlib
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from backend.domain.identity import utcnow

KEY_PREFIX = "nvlpat_"
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_RANDOM_LENGTH = 43  # 62**43 > 2**256
_CHECKSUM_LENGTH = 6  # 62**6 > 2**32
KEY_LENGTH = len(KEY_PREFIX) + _RANDOM_LENGTH + _CHECKSUM_LENGTH

DEFAULT_LIFETIME_DAYS = 90
MAX_LIFETIME_DAYS = 365
# Enough for every assistant a person uses; a bound so a script cannot mint thousands.
MAX_ACTIVE_KEYS_PER_MEMBER = 20
# Calls one key may make in a minute, through the shared rate limiter.
DEFAULT_CALLS_PER_MINUTE = 120
# Malformed or unknown keys from one address in a minute before it is told to wait.
FAILED_ATTEMPTS_PER_MINUTE = 30


class RevokedBecause(StrEnum):
    """Why a key stopped working: said in plain words wherever it is shown."""

    HOLDER = "holder"  # the person who made it withdrew it
    OWNER = "owner"  # a workspace owner withdrew it
    ACCESS_LOST = "access_lost"  # its holder no longer has access to what it reads
    ACCOUNT_CLOSED = "account_closed"  # its holder's account is no longer active


class AccessKeyStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class AccessKey(BaseModel):
    """A stored key. ``token_hash`` is the SHA-256 digest; the key itself is never kept."""

    id: str
    workspace_id: str
    user_id: str
    # None: every story its holder can read in the workspace.
    project_id: str | None = None
    name: str
    # The key's last four characters, so its holder can tell keys apart.
    hint: str
    may_suggest: bool = False
    token_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_reason: RevokedBecause | None = None

    def status(self, now: datetime | None = None) -> AccessKeyStatus:
        if self.revoked_at is not None:
            return AccessKeyStatus.REVOKED
        if self.expires_at <= (now or utcnow()):
            return AccessKeyStatus.EXPIRED
        return AccessKeyStatus.ACTIVE


class AccessKeyUse(BaseModel):
    """How often a key called one tool on one day. Counts only: never what was read."""

    day: str
    tool: str
    calls: int
    refused: int = 0


def _base62(number: int, width: int) -> str:
    digits: list[str] = []
    while number:
        number, digit = divmod(number, 62)
        digits.append(_ALPHABET[digit])
    return "".join(reversed(digits)).rjust(width, "0")


def _checksum(random_part: str) -> str:
    return _base62(zlib.crc32(random_part.encode("ascii")), _CHECKSUM_LENGTH)


def generate_key() -> str:
    """A new key: prefix, 256 random bits in base62, and a CRC32 checksum."""
    random_part = _base62(secrets.randbits(256), _RANDOM_LENGTH)
    return f"{KEY_PREFIX}{random_part}{_checksum(random_part)}"


def well_formed(key: str) -> bool:
    """Whether a presented key has the right shape and checksum (no lookup)."""
    if len(key) != KEY_LENGTH or not key.startswith(KEY_PREFIX):
        return False
    body = key[len(KEY_PREFIX) :]
    if any(character not in _ALPHABET for character in body):
        return False
    random_part, checksum = body[:_RANDOM_LENGTH], body[_RANDOM_LENGTH:]
    return secrets.compare_digest(_checksum(random_part), checksum)


def key_hint(key: str) -> str:
    return key[-4:]


def lifetime(days: int | None) -> timedelta:
    """The lifetime asked for, within bounds: 90 days unless said, never over a year."""
    chosen = DEFAULT_LIFETIME_DAYS if days is None else days
    if chosen < 1 or chosen > MAX_LIFETIME_DAYS:
        raise ValueError(f"A key lasts between 1 and {MAX_LIFETIME_DAYS} days.")
    return timedelta(days=chosen)


__all__ = [
    "DEFAULT_CALLS_PER_MINUTE",
    "DEFAULT_LIFETIME_DAYS",
    "FAILED_ATTEMPTS_PER_MINUTE",
    "KEY_LENGTH",
    "KEY_PREFIX",
    "MAX_ACTIVE_KEYS_PER_MEMBER",
    "MAX_LIFETIME_DAYS",
    "AccessKey",
    "AccessKeyStatus",
    "AccessKeyUse",
    "RevokedBecause",
    "generate_key",
    "key_hint",
    "lifetime",
    "well_formed",
]
