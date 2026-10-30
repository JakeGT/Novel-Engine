"""Envelope encryption for user-supplied provider credentials.

A key-encryption key (KEK) from configuration wraps a per-credential data key
(DEK); the DEK encrypts the credential with AES-256-GCM. Only ciphertext and the
wrapped DEK are persisted. The key version is recorded so KEKs can rotate.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEK_BYTES = 32


class CryptoError(RuntimeError):
    """Raised when a key cannot be encoded/decoded or a ciphertext fails to decrypt."""


@dataclass(frozen=True)
class Envelope:
    key_version: int
    wrapped_dek: str
    nonce: str
    ciphertext: str


def generate_kek() -> str:
    """Generate a new base64url-encoded 256-bit KEK (for operators/runbooks)."""
    return base64.urlsafe_b64encode(os.urandom(KEK_BYTES)).decode()


def _decode_kek(kek: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(kek.encode())
    except Exception as error:  # noqa: BLE001 - normalize any decode failure
        raise CryptoError("CREDENTIAL_KEK is not valid base64") from error
    if len(raw) != KEK_BYTES:
        raise CryptoError("CREDENTIAL_KEK must decode to exactly 32 bytes")
    return raw


def check_kek(kek: str) -> None:
    """Raise ``CryptoError`` unless ``kek`` is a usable key (32 bytes, URL-safe base64)."""
    _decode_kek(kek)


def key_fingerprint(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:16]


def key_last4(plaintext: str) -> str:
    return plaintext[-4:]


def envelope_encrypt(plaintext: str, *, kek: str, key_version: int) -> Envelope:
    kek_bytes = _decode_kek(kek)
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), None)
    wrapped_nonce = os.urandom(NONCE_BYTES)
    wrapped_dek = AESGCM(kek_bytes).encrypt(wrapped_nonce, dek, None)
    return Envelope(
        key_version=key_version,
        wrapped_dek=base64.urlsafe_b64encode(wrapped_nonce + wrapped_dek).decode(),
        nonce=base64.urlsafe_b64encode(nonce).decode(),
        ciphertext=base64.urlsafe_b64encode(ciphertext).decode(),
    )


def envelope_decrypt(envelope: Envelope, *, kek: str) -> str:
    kek_bytes = _decode_kek(kek)
    try:
        wrapped = base64.urlsafe_b64decode(envelope.wrapped_dek.encode())
        dek = AESGCM(kek_bytes).decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], None)
        plaintext = AESGCM(dek).decrypt(
            base64.urlsafe_b64decode(envelope.nonce.encode()),
            base64.urlsafe_b64decode(envelope.ciphertext.encode()),
            None,
        )
    except Exception as error:  # noqa: BLE001 - normalize any AEAD failure
        raise CryptoError("credential decryption failed") from error
    return plaintext.decode("utf-8")


def rewrap_envelope(
    envelope: Envelope, *, old_kek: str, new_kek: str, new_version: int
) -> Envelope:
    """Lock an envelope's data key under a new KEK without touching the ciphertext.

    The data key is unwrapped with ``old_kek`` and checked against the ciphertext
    (AES-GCM authenticates it, so a wrong key or a damaged row fails here, before
    anything is written); it is then wrapped again with ``new_kek``. The secret
    itself is decrypted only for that check and never returned.
    """
    old_bytes = _decode_kek(old_kek)
    new_bytes = _decode_kek(new_kek)
    try:
        wrapped = base64.urlsafe_b64decode(envelope.wrapped_dek.encode())
        dek = AESGCM(old_bytes).decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], None)
        AESGCM(dek).decrypt(
            base64.urlsafe_b64decode(envelope.nonce.encode()),
            base64.urlsafe_b64decode(envelope.ciphertext.encode()),
            None,
        )
    except Exception as error:  # noqa: BLE001 - normalize any AEAD failure
        raise CryptoError("credential decryption failed") from error
    wrapped_nonce = os.urandom(NONCE_BYTES)
    rewrapped = AESGCM(new_bytes).encrypt(wrapped_nonce, dek, None)
    return Envelope(
        key_version=new_version,
        wrapped_dek=base64.urlsafe_b64encode(wrapped_nonce + rewrapped).decode(),
        nonce=envelope.nonce,
        ciphertext=envelope.ciphertext,
    )
