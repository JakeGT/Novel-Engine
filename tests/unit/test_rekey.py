"""Re-locking stored provider keys under a new credential key (OPS-004)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.adapters.credentials import KeyVersionCount, RekeyedCredential, RekeyReport
from backend.core.config import Settings
from backend.core.crypto import (
    CryptoError,
    envelope_decrypt,
    envelope_encrypt,
    generate_kek,
    rewrap_envelope,
)
from backend.devtools import rekey

pytestmark = pytest.mark.unit


def test_rewrap_moves_the_data_key_and_keeps_the_ciphertext() -> None:
    old, new = generate_kek(), generate_kek()
    envelope = envelope_encrypt("sk-or-v1-kept", kek=old, key_version=1)

    moved = rewrap_envelope(envelope, old_kek=old, new_kek=new, new_version=2)

    assert moved.key_version == 2
    assert moved.ciphertext == envelope.ciphertext
    assert moved.nonce == envelope.nonce
    assert moved.wrapped_dek != envelope.wrapped_dek
    assert envelope_decrypt(moved, kek=new) == "sk-or-v1-kept"
    with pytest.raises(CryptoError):
        envelope_decrypt(moved, kek=old)


def test_rewrap_with_the_wrong_old_key_fails_before_anything_is_made() -> None:
    envelope = envelope_encrypt("secret", kek=generate_kek(), key_version=1)
    with pytest.raises(CryptoError):
        rewrap_envelope(envelope, old_kek=generate_kek(), new_kek=generate_kek(), new_version=2)


def test_rewrap_refuses_a_damaged_ciphertext() -> None:
    old = generate_kek()
    envelope = envelope_encrypt("secret", kek=old, key_version=1)
    damaged = envelope.__class__(
        key_version=1,
        wrapped_dek=envelope.wrapped_dek,
        nonce=envelope.nonce,
        ciphertext=envelope.ciphertext[:-4] + "AAAA",
    )
    with pytest.raises(CryptoError):
        rewrap_envelope(damaged, old_kek=old, new_kek=generate_kek(), new_version=2)


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_settings_without_a_key_are_refused_in_plain_words() -> None:
    code, result = asyncio.run(rekey.run("status", _settings(CREDENTIAL_KEK="")))
    assert code == 2
    assert "No credential key is set" in result["problem"]


def test_the_current_version_listed_again_with_another_key_is_refused() -> None:
    settings = _settings(
        CREDENTIAL_KEK=generate_kek(),
        CREDENTIAL_KEK_VERSION=2,
        CREDENTIAL_PREVIOUS_KEKS=f"2:{generate_kek()}",
    )
    code, result = asyncio.run(rekey.run("rekey", settings))
    assert code == 2
    assert "new version number" in result["problem"]


def test_a_malformed_older_key_is_refused_without_printing_it() -> None:
    settings = _settings(
        CREDENTIAL_KEK=generate_kek(),
        CREDENTIAL_KEK_VERSION=2,
        CREDENTIAL_PREVIOUS_KEKS="1:not-a-real-key",
    )
    code, result = asyncio.run(rekey.run("verify", settings))
    assert code == 2
    assert "version 1" in result["problem"]
    assert "not-a-real-key" not in json.dumps(result)


class _Vault:
    current_version = 2
    configured_versions = [1, 2]

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def key_versions(self) -> list[KeyVersionCount]:
        return [KeyVersionCount(1, "active", 3)]

    async def rekey(
        self, *, dry_run: bool, include_revoked: bool, audit_actor: str | None = None
    ) -> RekeyReport:
        self.calls.append(
            {"dry_run": dry_run, "include_revoked": include_revoked, "audit_actor": audit_actor}
        )
        return RekeyReport(
            current_version=2,
            dry_run=dry_run,
            examined=3,
            rekeyed=[RekeyedCredential("ws1", "c1", 1)],
        )


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record_audit(self, **event: object) -> None:
        self.events.append(event)


def test_a_dry_run_writes_no_audit_and_says_what_would_move() -> None:
    settings = _settings(CREDENTIAL_KEK=generate_kek(), CREDENTIAL_KEK_VERSION=2)
    container = SimpleNamespace(credentials=_Vault(), tenancy=_Audit())

    code, result = asyncio.run(rekey.run("rekey", settings, container=container))

    assert code == 0
    assert result["would_move"] == 1 and result["dry_run"] is True
    assert container.credentials.calls == [
        {"dry_run": True, "include_revoked": False, "audit_actor": "operator:rekey"}
    ]
    assert container.tenancy.events == []


def test_applying_has_the_vault_record_each_move_with_the_move_itself() -> None:
    # The audit events are written by the vault in each move's own transaction
    # (tests/integration/test_credential_vault.py), never afterwards one by one.
    settings = _settings(CREDENTIAL_KEK=generate_kek(), CREDENTIAL_KEK_VERSION=2)
    container = SimpleNamespace(credentials=_Vault(), tenancy=_Audit())

    code, result = asyncio.run(rekey.run("rekey", settings, apply=True, container=container))

    assert code == 0 and result["moved"] == 1
    assert container.credentials.calls == [
        {"dry_run": False, "include_revoked": False, "audit_actor": "operator:rekey"}
    ]
    assert container.tenancy.events == []


@pytest.mark.parametrize(
    ("previous", "place"),
    [
        ("1:{good},kept-without-a-version", "Entry 2"),
        ("one:{good}", "Entry 1"),
        ("1:", "Entry 1"),
        ("0:{good}", "Entry 1"),
        (" ,1:{good},,1:{other}", "Entry 4"),
    ],
)
def test_an_older_key_entry_that_cannot_be_read_is_refused_by_its_place(previous, place) -> None:
    good, other = generate_kek(), generate_kek()
    listed = previous.format(good=good, other=other)
    settings = _settings(
        CREDENTIAL_KEK=generate_kek(), CREDENTIAL_KEK_VERSION=3, CREDENTIAL_PREVIOUS_KEKS=listed
    )
    code, result = asyncio.run(rekey.run("status", settings))
    assert code == 2 and place in result["problem"]
    printed = json.dumps(result)
    for secret in (good, other, "kept-without-a-version"):
        assert secret not in printed
    # The server is refused the same way when it starts, rather than dropping the key.
    with pytest.raises(ValueError, match=place):
        _ = settings.previous_credential_keks


def test_an_empty_place_in_the_older_keys_is_not_an_error() -> None:
    good = generate_kek()
    settings = _settings(CREDENTIAL_PREVIOUS_KEKS=f"1:{good}, ,")
    assert settings.previous_credential_keks == {1: good}
