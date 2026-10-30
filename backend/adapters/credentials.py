"""PostgreSQL credential vault with envelope encryption.

Only ciphertext, the wrapped data key, and non-secret metadata are persisted.
Plaintext key material exists only transiently inside this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, delete, func, select, text, update
from sqlalchemy.orm import Mapped, mapped_column

from backend.adapters.system import uuid7
from backend.core.crypto import (
    CryptoError,
    Envelope,
    envelope_decrypt,
    envelope_encrypt,
    key_fingerprint,
    key_last4,
    rewrap_envelope,
)
from backend.core.database import Base, session_scope
from backend.domain.credentials import (
    Credential,
    CredentialNotFound,
    CredentialProvider,
    CredentialStatus,
    CredentialUnavailable,
)


class CredentialRow(Base):
    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, index=True)
    owner_user_id: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String)
    key_fingerprint: Mapped[str] = mapped_column(String)
    key_last4: Mapped[str] = mapped_column(String)
    key_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="active")
    wrapped_dek: Mapped[str] = mapped_column(Text)
    nonce: Mapped[str] = mapped_column(Text)
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@dataclass(frozen=True)
class KeyVersionCount:
    """How many stored keys one credential-key version locks, by status. No key is shown."""

    key_version: int
    status: str
    count: int


@dataclass(frozen=True)
class RekeyedCredential:
    workspace_id: str
    credential_id: str
    from_version: int


@dataclass
class RekeyReport:
    """What re-locking stored keys under the current credential key did (OPS-004).

    Ids only: never a key, its last characters or its fingerprint.
    """

    current_version: int
    dry_run: bool
    examined: int = 0
    already_current: int = 0
    rekeyed: list[RekeyedCredential] = field(default_factory=list)
    skipped_revoked: int = 0
    # Keys locked by a version this server was not given (CREDENTIAL_PREVIOUS_KEKS).
    missing_version: dict[int, list[str]] = field(default_factory=dict)
    # Keys the configured key for their version could not open, or that opened to a
    # different key than the one recorded: a wrong key or a damaged row.
    unopenable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_version and not self.unopenable


@dataclass
class VerifyReport:
    """Whether every stored key opens with the keys this server holds (OPS-004)."""

    current_version: int
    configured_versions: list[int]
    by_version: list[KeyVersionCount]
    checked: int = 0
    opened: int = 0
    missing_version: dict[int, list[str]] = field(default_factory=dict)
    unopenable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_version and not self.unopenable

    @property
    def retirable_versions(self) -> list[int]:
        """Older versions no active key needs any more: safe to remove from the settings."""
        in_use = {
            item.key_version
            for item in self.by_version
            if item.status == CredentialStatus.ACTIVE.value and item.count
        }
        return sorted(
            version
            for version in self.configured_versions
            if version != self.current_version and version not in in_use
        )

    @property
    def old_active(self) -> int:
        """Active keys still locked by an older version."""
        return sum(
            item.count
            for item in self.by_version
            if item.status == CredentialStatus.ACTIVE.value
            and item.key_version != self.current_version
        )


def _envelope(row: CredentialRow) -> Envelope:
    return Envelope(
        key_version=row.key_version,
        wrapped_dek=row.wrapped_dek,
        nonce=row.nonce,
        ciphertext=row.ciphertext,
    )


def _to_credential(row: CredentialRow) -> Credential:
    return Credential(
        id=row.id,
        workspace_id=row.workspace_id,
        owner_user_id=row.owner_user_id,
        provider=CredentialProvider(row.provider),
        label=row.label,
        key_fingerprint=row.key_fingerprint,
        key_last4=row.key_last4,
        key_version=row.key_version,
        status=CredentialStatus(row.status),
        created_at=row.created_at,
        rotated_at=row.rotated_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class PostgresCredentialVault:
    def __init__(
        self,
        session_factory: Any,
        *,
        kek: str = "",
        key_version: int = 1,
        previous_keks: dict[int, str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._kek = kek
        self._key_version = key_version
        # Older KEKs remain available for decryption so rotating the current KEK
        # does not strand existing credentials.
        self._keks: dict[int, str] = dict(previous_keks or {})
        if kek:
            self._keks[key_version] = kek

    @property
    def is_configured(self) -> bool:
        return bool(self._kek)

    def _require_configured(self) -> None:
        if not self.is_configured:
            raise CredentialUnavailable(
                "CREDENTIAL_KEK is not configured; credentials are disabled"
            )

    @staticmethod
    def _activation_lock_key(workspace_id: str, provider: str) -> str:
        return f"credential-active:{workspace_id}:{provider}"

    async def _lock_activation(self, session: Any, workspace_id: str, provider: str) -> None:
        # Serialize replacement even when no active row exists yet; row locks alone
        # cannot protect that empty-set race.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": self._activation_lock_key(workspace_id, provider)},
        )

    async def _revoke_other_active(
        self,
        session: Any,
        *,
        workspace_id: str,
        provider: str,
        except_id: str | None = None,
        now: datetime,
    ) -> None:
        predicate = [
            CredentialRow.workspace_id == workspace_id,
            CredentialRow.provider == provider,
            CredentialRow.status == CredentialStatus.ACTIVE.value,
        ]
        if except_id is not None:
            predicate.append(CredentialRow.id != except_id)
        await session.execute(
            update(CredentialRow)
            .where(*predicate)
            .values(status=CredentialStatus.REVOKED.value, revoked_at=now)
        )

    async def store(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        provider: CredentialProvider,
        label: str,
        plaintext_key: str,
    ) -> Credential:
        self._require_configured()
        envelope = envelope_encrypt(plaintext_key, kek=self._kek, key_version=self._key_version)
        now = datetime.now(UTC)
        row = CredentialRow(
            id=str(uuid7()),
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            provider=provider.value,
            label=label,
            key_fingerprint=key_fingerprint(plaintext_key),
            key_last4=key_last4(plaintext_key),
            key_version=envelope.key_version,
            status=CredentialStatus.ACTIVE.value,
            wrapped_dek=envelope.wrapped_dek,
            nonce=envelope.nonce,
            ciphertext=envelope.ciphertext,
            created_at=now,
        )
        async with session_scope(self._session_factory) as session:
            await self._lock_activation(session, workspace_id, provider.value)
            await self._revoke_other_active(
                session,
                workspace_id=workspace_id,
                provider=provider.value,
                now=now,
            )
            session.add(row)
            await session.flush()
        return _to_credential(row)

    async def list_for_workspace(self, workspace_id: str) -> list[Credential]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    select(CredentialRow)
                    .where(CredentialRow.workspace_id == workspace_id)
                    .order_by(CredentialRow.created_at)
                )
            ).scalars()
            return [_to_credential(row) for row in rows]

    async def get(self, *, workspace_id: str, credential_id: str) -> Credential | None:
        row = await self._get_row(workspace_id, credential_id)
        return _to_credential(row) if row else None

    async def rotate(
        self, *, workspace_id: str, credential_id: str, plaintext_key: str
    ) -> Credential:
        self._require_configured()
        envelope = envelope_encrypt(plaintext_key, kek=self._kek, key_version=self._key_version)
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    select(CredentialRow).where(
                        CredentialRow.id == credential_id,
                        CredentialRow.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise CredentialNotFound("Credential not found.")
            now = datetime.now(UTC)
            await self._lock_activation(session, workspace_id, row.provider)
            await self._revoke_other_active(
                session,
                workspace_id=workspace_id,
                provider=row.provider,
                except_id=row.id,
                now=now,
            )
            row.key_fingerprint = key_fingerprint(plaintext_key)
            row.key_last4 = key_last4(plaintext_key)
            row.key_version = envelope.key_version
            row.wrapped_dek = envelope.wrapped_dek
            row.nonce = envelope.nonce
            row.ciphertext = envelope.ciphertext
            row.rotated_at = now
            row.status = CredentialStatus.ACTIVE.value
            row.revoked_at = None
            await session.flush()
            return _to_credential(row)

    async def revoke(self, *, workspace_id: str, credential_id: str) -> Credential:
        row = await self._get_row(workspace_id, credential_id)
        if row is None:
            raise CredentialNotFound("Credential not found.")
        async with session_scope(self._session_factory) as session:
            attached = await session.get(CredentialRow, credential_id)
            assert attached is not None
            attached.status = CredentialStatus.REVOKED.value
            attached.revoked_at = datetime.now(UTC)
            await session.flush()
            return _to_credential(attached)

    async def delete(self, *, workspace_id: str, credential_id: str) -> bool:
        async with session_scope(self._session_factory) as session:
            row = await session.get(CredentialRow, credential_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            await session.delete(row)
            return True

    async def resolve_plaintext(
        self, *, workspace_id: str, credential_id: str, actor_user_id: str
    ) -> str:
        self._require_configured()
        row = await self._get_row(workspace_id, credential_id)
        if row is None or row.status != CredentialStatus.ACTIVE.value:
            raise CredentialNotFound("Active credential not found.")
        envelope = _envelope(row)
        kek = self._keks.get(row.key_version)
        if not kek:
            raise CredentialUnavailable(
                f"no key-encryption key configured for version {row.key_version}"
            )
        return envelope_decrypt(envelope, kek=kek)

    async def mark_used(self, *, workspace_id: str, credential_id: str) -> None:
        from sqlalchemy import select

        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    select(CredentialRow).where(
                        CredentialRow.id == credential_id,
                        CredentialRow.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.last_used_at = datetime.now(UTC)

    async def _get_row(self, workspace_id: str, credential_id: str) -> CredentialRow | None:
        from sqlalchemy import select

        async with session_scope(self._session_factory) as session:
            return (
                await session.execute(
                    select(CredentialRow).where(
                        CredentialRow.id == credential_id,
                        CredentialRow.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()

    async def delete_workspace(self, workspace_id: str) -> int:
        async with session_scope(self._session_factory) as session:
            result = await session.execute(
                delete(CredentialRow).where(CredentialRow.workspace_id == workspace_id)
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    # -- Re-locking under a new credential key (OPS-004) ------------------------------

    @property
    def current_version(self) -> int:
        return self._key_version

    @property
    def configured_versions(self) -> list[int]:
        return sorted(self._keks)

    async def key_versions(self) -> list[KeyVersionCount]:
        async with session_scope(self._session_factory) as session:
            rows = await session.execute(
                select(CredentialRow.key_version, CredentialRow.status, func.count())
                .group_by(CredentialRow.key_version, CredentialRow.status)
                .order_by(CredentialRow.key_version, CredentialRow.status)
            )
            return [
                KeyVersionCount(key_version=int(version), status=str(status), count=int(count))
                for version, status, count in rows
            ]

    @staticmethod
    def _opens(envelope: Envelope, fingerprint: str, kek: str) -> bool:
        """The envelope opens with ``kek`` to the key ``fingerprint`` records."""
        try:
            plaintext = envelope_decrypt(envelope, kek=kek)
        except CryptoError:
            return False
        return key_fingerprint(plaintext) == fingerprint

    async def rekey(
        self,
        *,
        dry_run: bool = True,
        include_revoked: bool = False,
        audit_actor: str | None = None,
    ) -> RekeyReport:
        """Re-lock every stored key on an older version under the current credential key.

        Only each key's data key is unwrapped and wrapped again; the stored ciphertext is
        unchanged. A row is written only after its new envelope has been opened with the
        current key and found to hold the key its fingerprint records, one row per
        transaction under a row lock, so an owner connecting a key at the same time, a
        second run, or a stop part way leaves every row either as it was or moved. A dry
        run opens each key with its old version and writes nothing. Revoked keys are left
        as they are unless asked: they can never be used, and retiring the old key makes
        them unreadable, which is usually what is wanted.

        With ``audit_actor``, each move's ``credential.rekeyed`` audit event is written in
        the same transaction as the move, so a key is never moved without its record (or
        recorded without being moved), even when the run stops part way.
        """
        self._require_configured()
        report = RekeyReport(current_version=self._key_version, dry_run=dry_run)
        async with session_scope(self._session_factory) as session:
            candidates = list(
                await session.execute(
                    select(
                        CredentialRow.id, CredentialRow.key_version, CredentialRow.status
                    ).order_by(CredentialRow.id)
                )
            )
        for credential_id, version, status in candidates:
            report.examined += 1
            if version == self._key_version:
                report.already_current += 1
                continue
            if status != CredentialStatus.ACTIVE.value and not include_revoked:
                report.skipped_revoked += 1
                continue
            old_kek = self._keks.get(version)
            if not old_kek:
                report.missing_version.setdefault(version, []).append(credential_id)
                continue
            async with session_scope(self._session_factory) as session:
                row = (
                    await session.execute(
                        select(CredentialRow)
                        .where(CredentialRow.id == credential_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    continue
                if row.key_version == self._key_version:
                    # Connected again (or moved by another run) since the list was read.
                    report.already_current += 1
                    continue
                if row.key_version != version:
                    old_kek = self._keks.get(row.key_version, "")
                    if not old_kek:
                        report.missing_version.setdefault(row.key_version, []).append(row.id)
                        continue
                if not self._opens(_envelope(row), row.key_fingerprint, old_kek):
                    report.unopenable.append(row.id)
                    continue
                try:
                    moved = rewrap_envelope(
                        _envelope(row),
                        old_kek=old_kek,
                        new_kek=self._kek,
                        new_version=self._key_version,
                    )
                except CryptoError:
                    report.unopenable.append(row.id)
                    continue
                if not self._opens(moved, row.key_fingerprint, self._kek):
                    report.unopenable.append(row.id)
                    continue
                report.rekeyed.append(
                    RekeyedCredential(
                        workspace_id=row.workspace_id,
                        credential_id=row.id,
                        from_version=row.key_version,
                    )
                )
                if dry_run:
                    continue
                from_version = row.key_version
                row.wrapped_dek = moved.wrapped_dek
                row.key_version = moved.key_version
                if audit_actor is not None:
                    from backend.adapters.tenancy_store import AuditEventRow

                    session.add(
                        AuditEventRow(
                            id=str(uuid7()),
                            workspace_id=row.workspace_id,
                            actor_user_id=audit_actor,
                            action="credential.rekeyed",
                            target_type="credential",
                            target_id=row.id,
                            detail={
                                "from_version": from_version,
                                "to_version": self._key_version,
                            },
                            correlation_id="",
                            created_at=datetime.now(UTC),
                        )
                    )
                await session.flush()
        return report

    async def verify(self, *, include_revoked: bool = False) -> VerifyReport:
        """Open every active stored key (and revoked ones if asked) with the keys held here.

        Nothing is written, and no key leaves this method.
        """
        report = VerifyReport(
            current_version=self._key_version,
            configured_versions=self.configured_versions,
            by_version=await self.key_versions(),
        )
        statement = select(CredentialRow).order_by(CredentialRow.id)
        if not include_revoked:
            statement = statement.where(CredentialRow.status == CredentialStatus.ACTIVE.value)
        async with session_scope(self._session_factory) as session:
            rows = list((await session.execute(statement)).scalars())
        for row in rows:
            report.checked += 1
            kek = self._keks.get(row.key_version)
            if not kek:
                report.missing_version.setdefault(row.key_version, []).append(row.id)
            elif self._opens(_envelope(row), row.key_fingerprint, kek):
                report.opened += 1
            else:
                report.unopenable.append(row.id)
        return report
