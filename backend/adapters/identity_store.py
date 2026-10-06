"""PostgreSQL-backed identity, session, and login-attempt stores.

These share the operational database engine owned by ``PostgresDatabase``. Raw
session tokens are never stored: only their SHA-256 hash is persisted.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from backend.adapters.system import uuid7
from backend.core.database import Base, session_scope
from backend.core.security import generate_token, hash_token
from backend.domain.identity import IdentityClaims, LoginAttempt, Session, User, utcnow


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalIdentityRow(Base):
    __tablename__ = "external_identities"

    issuer: Mapped[str] = mapped_column(Text, primary_key=True)
    subject: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("sessions_user_id_idx", "user_id"),
        Index("sessions_expires_idx", "expires_at"),
    )

    id_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"), nullable=False)
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_from: Mapped[str | None] = mapped_column(Text, nullable=True)


class LoginAttemptRow(Base):
    __tablename__ = "login_attempts"

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        is_active=row.is_active,
        created_at=row.created_at,
    )


class PostgresIdentityStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create_user(self, claims: IdentityClaims) -> User:
        async with session_scope(self._session_factory) as session:
            identity = (
                await session.execute(
                    select(ExternalIdentityRow).where(
                        ExternalIdentityRow.issuer == claims.issuer,
                        ExternalIdentityRow.subject == claims.subject,
                    )
                )
            ).scalar_one_or_none()

            if identity is not None:
                user_row = (
                    await session.execute(select(UserRow).where(UserRow.id == identity.user_id))
                ).scalar_one()
                identity.email = claims.email
                identity.display_name = claims.display_name
                user_row.email = claims.email
                user_row.display_name = claims.display_name
                return _to_user(user_row)

            user_row = UserRow(
                id=str(uuid7()),
                email=claims.email,
                display_name=claims.display_name,
                is_active=True,
                created_at=utcnow(),
            )
            session.add(user_row)
            # Flush the user first: external_identities has a FK but no ORM
            # relationship, so ordering is not inferred by the unit of work.
            await session.flush()
            session.add(
                ExternalIdentityRow(
                    issuer=claims.issuer,
                    subject=claims.subject,
                    user_id=user_row.id,
                    email=claims.email,
                    display_name=claims.display_name,
                    linked_at=utcnow(),
                )
            )
            await session.flush()
            return _to_user(user_row)

    async def get_user(self, user_id: str) -> User | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.id == user_id))
            ).scalar_one_or_none()
            return _to_user(row) if row else None

    async def get_user_for_identity(self, issuer: str, subject: str) -> User | None:
        async with session_scope(self._session_factory) as session:
            identity = (
                await session.execute(
                    select(ExternalIdentityRow).where(
                        ExternalIdentityRow.issuer == issuer,
                        ExternalIdentityRow.subject == subject,
                    )
                )
            ).scalar_one_or_none()
            if identity is None:
                return None
            row = (
                await session.execute(select(UserRow).where(UserRow.id == identity.user_id))
            ).scalar_one_or_none()
            return _to_user(row) if row else None


class PostgresSessionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        user_id: str,
        ttl_seconds: int,
        csrf_token: str,
        rotated_from: str | None = None,
    ) -> Session:
        raw_token = generate_token()
        now = utcnow()
        expires_at = now + timedelta(seconds=ttl_seconds)
        async with session_scope(self._session_factory) as session:
            session.add(
                SessionRow(
                    id_hash=hash_token(raw_token),
                    user_id=user_id,
                    csrf_token=csrf_token,
                    created_at=now,
                    expires_at=expires_at,
                    rotated_from=rotated_from,
                )
            )
        return Session(
            id=raw_token,
            user_id=user_id,
            csrf_token=csrf_token,
            created_at=now,
            expires_at=expires_at,
            rotated_from=rotated_from,
        )

    async def get(self, session_id: str) -> Session | None:
        id_hash = hash_token(session_id)
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(select(SessionRow).where(SessionRow.id_hash == id_hash))
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.expires_at <= utcnow():
                await session.execute(delete(SessionRow).where(SessionRow.id_hash == id_hash))
                return None
            return Session(
                id=session_id,
                user_id=row.user_id,
                csrf_token=row.csrf_token,
                created_at=row.created_at,
                expires_at=row.expires_at,
                rotated_from=row.rotated_from,
            )

    async def revoke(self, session_id: str) -> bool:
        async with session_scope(self._session_factory) as session:
            result = await session.execute(
                delete(SessionRow).where(SessionRow.id_hash == hash_token(session_id))
            )
            return (result.rowcount or 0) > 0

    async def revoke_all_for_user(self, user_id: str) -> int:
        async with session_scope(self._session_factory) as session:
            result = await session.execute(delete(SessionRow).where(SessionRow.user_id == user_id))
            return int(result.rowcount or 0)


class PostgresLoginAttemptStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
        redirect_uri: str,
        ttl_seconds: int,
    ) -> LoginAttempt:
        now = utcnow()
        expires_at = now + timedelta(seconds=ttl_seconds)
        async with session_scope(self._session_factory) as session:
            session.add(
                LoginAttemptRow(
                    state=state,
                    nonce=nonce,
                    code_verifier=code_verifier,
                    redirect_uri=redirect_uri,
                    created_at=now,
                    expires_at=expires_at,
                )
            )
        return LoginAttempt(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            created_at=now,
            expires_at=expires_at,
        )

    async def consume(self, state: str) -> LoginAttempt | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(select(LoginAttemptRow).where(LoginAttemptRow.state == state))
            ).scalar_one_or_none()
            if row is None:
                return None
            await session.execute(delete(LoginAttemptRow).where(LoginAttemptRow.state == state))
            attempt = LoginAttempt(
                state=row.state,
                nonce=row.nonce,
                code_verifier=row.code_verifier,
                redirect_uri=row.redirect_uri,
                created_at=row.created_at,
                expires_at=row.expires_at,
            )
            if attempt.expires_at <= utcnow():
                return None
            return attempt
