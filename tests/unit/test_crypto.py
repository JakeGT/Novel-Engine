import pytest

from backend.core.crypto import (
    CryptoError,
    envelope_decrypt,
    envelope_encrypt,
    generate_kek,
    key_fingerprint,
    key_last4,
)

pytestmark = pytest.mark.unit


def test_envelope_round_trip_hides_plaintext() -> None:
    kek = generate_kek()
    envelope = envelope_encrypt("sk-or-v1-supersecret", kek=kek, key_version=3)

    assert envelope.key_version == 3
    assert "sk-or-v1-supersecret" not in envelope.ciphertext
    assert envelope_decrypt(envelope, kek=kek) == "sk-or-v1-supersecret"


def test_wrong_kek_cannot_decrypt() -> None:
    envelope = envelope_encrypt("secret", kek=generate_kek(), key_version=1)
    with pytest.raises(CryptoError):
        envelope_decrypt(envelope, kek=generate_kek())


def test_tampered_ciphertext_is_rejected() -> None:
    kek = generate_kek()
    envelope = envelope_encrypt("secret", kek=kek, key_version=1)
    tampered = envelope.__class__(
        key_version=envelope.key_version,
        wrapped_dek=envelope.wrapped_dek,
        nonce=envelope.nonce,
        ciphertext=envelope.ciphertext[:-4] + "AAAA",
    )
    with pytest.raises(CryptoError):
        envelope_decrypt(tampered, kek=kek)


def test_fingerprint_is_stable_and_last4() -> None:
    assert key_last4("sk-or-v1-abcd1234") == "1234"
    assert key_fingerprint("sk-or-v1-abcd1234") == key_fingerprint("sk-or-v1-abcd1234")
    assert len(key_fingerprint("sk-or-v1-abcd1234")) == 16
    assert key_fingerprint("a") != key_fingerprint("b")
