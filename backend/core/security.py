"""Small security primitives shared by authentication code."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

MIN_VERIFIER_LENGTH = 43


def generate_token(nbytes: int = 32) -> str:
    """Return a URL-safe random token (session ids, CSRF tokens, OAuth state)."""
    return secrets.token_urlsafe(nbytes)


def generate_pkce_pair(length: int = 64) -> tuple[str, str]:
    """Return a (code_verifier, code_challenge) S256 PKCE pair."""
    if length < MIN_VERIFIER_LENGTH:
        length = MIN_VERIFIER_LENGTH
    verifier = secrets.token_urlsafe(length)[:length]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def hash_token(token: str) -> str:
    """Hash a bearer token for storage so a database leak cannot replay sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
