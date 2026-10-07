"""PostgreSQL credential vault with envelope encryption.

Only ciphertext, the wrapped data key, and non-secret metadata are persisted.
Plaintext key material exists only transiently inside this adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, delete
from sqlalchemy.orm import Mapped, mapped_column

from backend.adapters.system import uuid7
from backend.core.crypto import envelope_decrypt, envelope_encrypt, key_fingerprint, key_last4
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
            created_at=datetime.now(UTC),
        )
        async with session_scope(self._session_factory) as session:
            session.add(row)
            await session.flush()
        return _to_credential(row)

    async def list_for_workspace(self, workspace_id: str) -> list[Credential]:
        from sqlalchemy import select

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
        from sqlalchemy import select

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
            row.key_fingerprint = key_fingerprint(plaintext_key)
            row.key_last4 = key_last4(plaintext_key)
            row.key_version = envelope.key_version
            row.wrapped_dek = envelope.wrapped_dek
            row.nonce = envelope.nonce
            row.ciphertext = envelope.ciphertext
            row.rotated_at = datetime.now(UTC)
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
        from backend.core.crypto import Envelope

        envelope = Envelope(
            key_version=row.key_version,
            wrapped_dek=row.wrapped_dek,
            nonce=row.nonce,
            ciphertext=row.ciphertext,
        )
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
