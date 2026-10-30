"""PostgreSQL store for the site's search-by-meaning setup and its sealed key.

The setup is one row; the key is sealed exactly like workspace credentials (envelope
encryption under ``CREDENTIAL_KEK``) and only its metadata is ever read back. Reads are
synchronous single-row psycopg calls, like the embedding index store, so the delegating
embedder can re-read the setup cheaply without leaving the event loop. Nothing here
logs or returns a key.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg

from backend.adapters.credentials import (
    KeyVersionCount,
    RekeyedCredential,
    RekeyReport,
    VerifyReport,
)
from backend.core.crypto import (
    CryptoError,
    Envelope,
    envelope_decrypt,
    envelope_encrypt,
    key_fingerprint,
    key_last4,
    rewrap_envelope,
)
from backend.core.logging import log_event
from backend.domain.credentials import CredentialUnavailable
from backend.domain.site_search import SiteSearchSecret, SiteSearchSetup

logger = logging.getLogger(__name__)

# The single setup row and the single key row share one fixed id.
_SETUP_ID = 1
_SECRET_ID = "site"

_SETUP_COLUMNS = "provider, model, dimension, base_url, version, updated_by, updated_at, verified"


def _setup(row: Any) -> SiteSearchSetup:
    return SiteSearchSetup(
        provider=row[0],
        model=row[1],
        dimension=int(row[2]),
        base_url=row[3],
        version=int(row[4]),
        updated_by=row[5],
        updated_at=row[6],
        verified=row[7],
    )


def _secret(row: Any) -> SiteSearchSecret:
    return SiteSearchSecret(
        id=row[0],
        key_fingerprint=row[1],
        key_last4=row[2],
        key_version=int(row[3]),
        provider=row[4],
        host=row[5],
        created_by=row[6],
        updated_at=row[7],
    )


class SiteSearchStore:
    def __init__(
        self,
        dsn: str,
        *,
        kek: str = "",
        key_version: int = 1,
        previous_keks: dict[int, str] | None = None,
    ) -> None:
        self._dsn = dsn
        self._kek = kek
        self._key_version = key_version
        self._keks: dict[int, str] = dict(previous_keks or {})
        if kek:
            self._keks[key_version] = kek

    @property
    def is_configured(self) -> bool:
        return bool(self._kek)

    @property
    def current_version(self) -> int:
        return self._key_version

    @property
    def configured_versions(self) -> list[int]:
        return sorted(self._keks)

    def _require_configured(self) -> None:
        if not self.is_configured:
            raise CredentialUnavailable(
                "CREDENTIAL_KEK is not configured; credentials are disabled"
            )

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self._dsn) as connection:
            yield connection

    # --- the setup ---------------------------------------------------------------

    def get_setup(self) -> SiteSearchSetup | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_SETUP_COLUMNS} FROM site_search_setup WHERE id = %s", (_SETUP_ID,)
            ).fetchone()
        return _setup(row) if row else None

    def current_version_number(self) -> int:
        """The setup's version, or 0 when nothing is saved (a cheap poll for changes)."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT version FROM site_search_setup WHERE id = %s", (_SETUP_ID,)
            ).fetchone()
        return int(row[0]) if row else 0

    def save_setup(
        self,
        *,
        provider: str,
        model: str,
        dimension: int,
        base_url: str | None,
        updated_by: str,
        verified: bool | None,
    ) -> SiteSearchSetup:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO site_search_setup "
                "(id, provider, model, dimension, base_url, version, updated_by, verified) "
                "VALUES (%s, %s, %s, %s, %s, 1, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET provider = EXCLUDED.provider, "
                "model = EXCLUDED.model, dimension = EXCLUDED.dimension, "
                "base_url = EXCLUDED.base_url, version = site_search_setup.version + 1, "
                "updated_by = EXCLUDED.updated_by, updated_at = now(), "
                "verified = EXCLUDED.verified",
                (_SETUP_ID, provider, model, dimension, base_url, updated_by, verified),
            )
            row = connection.execute(
                f"SELECT {_SETUP_COLUMNS} FROM site_search_setup WHERE id = %s", (_SETUP_ID,)
            ).fetchone()
        assert row is not None
        return _setup(row)

    # --- the key -----------------------------------------------------------------

    def get_secret(self) -> SiteSearchSecret | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, key_fingerprint, key_last4, key_version, provider, host, "
                "created_by, updated_at FROM site_search_secrets WHERE id = %s",
                (_SECRET_ID,),
            ).fetchone()
        return _secret(row) if row else None

    def save_secret(
        self, *, provider: str, host: str, plaintext_key: str, created_by: str
    ) -> SiteSearchSecret:
        self._require_configured()
        envelope = envelope_encrypt(plaintext_key, kek=self._kek, key_version=self._key_version)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO site_search_secrets (id, key_fingerprint, key_last4, key_version, "
                "wrapped_dek, nonce, ciphertext, provider, host, created_by, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
                "ON CONFLICT (id) DO UPDATE SET key_fingerprint = EXCLUDED.key_fingerprint, "
                "key_last4 = EXCLUDED.key_last4, key_version = EXCLUDED.key_version, "
                "wrapped_dek = EXCLUDED.wrapped_dek, nonce = EXCLUDED.nonce, "
                "ciphertext = EXCLUDED.ciphertext, provider = EXCLUDED.provider, "
                "host = EXCLUDED.host, created_by = EXCLUDED.created_by, updated_at = now()",
                (
                    _SECRET_ID,
                    key_fingerprint(plaintext_key),
                    key_last4(plaintext_key),
                    envelope.key_version,
                    envelope.wrapped_dek,
                    envelope.nonce,
                    envelope.ciphertext,
                    provider,
                    host,
                    created_by,
                ),
            )
            row = connection.execute(
                "SELECT id, key_fingerprint, key_last4, key_version, provider, host, "
                "created_by, updated_at FROM site_search_secrets WHERE id = %s",
                (_SECRET_ID,),
            ).fetchone()
        assert row is not None
        return _secret(row)

    def resolve_plaintext(self) -> str:
        """Open the stored key. Only the authorized path (building the embedder)."""
        self._require_configured()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT key_version, wrapped_dek, nonce, ciphertext "
                "FROM site_search_secrets WHERE id = %s",
                (_SECRET_ID,),
            ).fetchone()
        if row is None:
            raise CredentialUnavailable("no site embedding key is stored")
        key_version = int(row[0])
        kek = self._keks.get(key_version)
        if not kek:
            raise CredentialUnavailable(
                f"no key-encryption key configured for version {key_version}"
            )
        return envelope_decrypt(
            Envelope(key_version=key_version, wrapped_dek=row[1], nonce=row[2], ciphertext=row[3]),
            kek=kek,
        )

    def delete_secret(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM site_search_secrets")

    def delete_all(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM site_search_secrets")
            connection.execute("DELETE FROM site_search_setup")

    # --- re-locking under a new credential key (OPS-004) --------------------------

    def key_versions(self) -> list[KeyVersionCount]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT key_version FROM site_search_secrets WHERE id = %s", (_SECRET_ID,)
            ).fetchone()
        if row is None:
            return []
        return [KeyVersionCount(key_version=int(row[0]), status="active", count=1)]

    @staticmethod
    def _opens(envelope: Envelope, fingerprint: str, kek: str) -> bool:
        try:
            plaintext = envelope_decrypt(envelope, kek=kek)
        except CryptoError:
            return False
        return key_fingerprint(plaintext) == fingerprint

    def rekey(self, *, dry_run: bool = True, audit_actor: str | None = None) -> RekeyReport:
        self._require_configured()
        report = RekeyReport(current_version=self._key_version, dry_run=dry_run)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT key_fingerprint, key_version, wrapped_dek, nonce, ciphertext "
                "FROM site_search_secrets WHERE id = %s",
                (_SECRET_ID,),
            ).fetchone()
        if row is None:
            return report
        report.examined += 1
        version = int(row[1])
        if version == self._key_version:
            report.already_current += 1
            return report
        old_kek = self._keks.get(version)
        if not old_kek:
            report.missing_version.setdefault(version, []).append(_SECRET_ID)
            return report
        envelope = Envelope(
            key_version=version, wrapped_dek=row[2], nonce=row[3], ciphertext=row[4]
        )
        if not self._opens(envelope, row[0], old_kek):
            report.unopenable.append(_SECRET_ID)
            return report
        try:
            moved = rewrap_envelope(
                envelope, old_kek=old_kek, new_kek=self._kek, new_version=self._key_version
            )
        except CryptoError:
            report.unopenable.append(_SECRET_ID)
            return report
        if not self._opens(moved, row[0], self._kek):
            report.unopenable.append(_SECRET_ID)
            return report
        report.rekeyed.append(
            RekeyedCredential(workspace_id="", credential_id=_SECRET_ID, from_version=version)
        )
        if not dry_run:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE site_search_secrets SET wrapped_dek = %s, key_version = %s, "
                    "updated_at = now() WHERE id = %s",
                    (moved.wrapped_dek, moved.key_version, _SECRET_ID),
                )
            log_event(
                logger,
                "site.search_key_rekeyed",
                from_version=version,
                to_version=self._key_version,
            )
        return report

    def verify(self, *, include_revoked: bool = False) -> VerifyReport:
        report = VerifyReport(
            current_version=self._key_version,
            configured_versions=self.configured_versions,
            by_version=self.key_versions(),
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT key_fingerprint, key_version, wrapped_dek, nonce, ciphertext "
                "FROM site_search_secrets WHERE id = %s",
                (_SECRET_ID,),
            ).fetchone()
        if row is None:
            return report
        report.checked += 1
        version = int(row[1])
        kek = self._keks.get(version)
        if not kek:
            report.missing_version.setdefault(version, []).append(_SECRET_ID)
        elif self._opens(
            Envelope(key_version=version, wrapped_dek=row[2], nonce=row[3], ciphertext=row[4]),
            row[0],
            kek,
        ):
            report.opened += 1
        else:
            report.unopenable.append(_SECRET_ID)
        return report


__all__ = ["SiteSearchStore"]
