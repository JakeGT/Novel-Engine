"""PostgreSQL-backed tenancy store.

Enforces the durable invariants for workspaces, memberships, projects,
invitations, and audit events, translating unique violations and invalid
ownership transitions into the typed errors from ``backend.domain.tenancy``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, delete, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from backend.adapters.system import uuid7
from backend.core.database import Base, session_scope
from backend.domain.identity import Role, utcnow
from backend.domain.tenancy import (
    AuditEvent,
    Invitation,
    InvitationConflict,
    InvitationNotAcceptable,
    InvitationNotFound,
    Membership,
    MembershipConflict,
    Permission,
    Project,
    ProjectSlugConflict,
    RoleTransitionError,
    Workspace,
    WorkspaceSlugConflict,
    ensure_member_removable,
    ensure_ownership_transfer,
    ensure_role_change_allowed,
    role_allows,
)


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MembershipRow(Base):
    __tablename__ = "memberships"
    __table_args__ = (Index("memberships_user_id_idx", "user_id"),)

    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectRow(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("projects_workspace_id_idx", "workspace_id"),
        UniqueConstraint("workspace_id", "slug", name="projects_workspace_slug_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InvitationRow(Base):
    __tablename__ = "invitations"
    __table_args__ = (Index("invitations_workspace_id_idx", "workspace_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_by_user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_user_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("users.id"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("audit_events_workspace_created_idx", "workspace_id", "created_at"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _to_workspace(row: WorkspaceRow) -> Workspace:
    return Workspace(
        id=row.id,
        slug=row.slug,
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_membership(row: MembershipRow) -> Membership:
    return Membership(
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        role=Role(row.role),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_project(row: ProjectRow) -> Project:
    return Project(
        id=row.id,
        workspace_id=row.workspace_id,
        slug=row.slug,
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_invitation(row: InvitationRow) -> Invitation:
    return Invitation(
        id=row.id,
        workspace_id=row.workspace_id,
        role=Role(row.role),
        token_hash=row.token_hash,
        created_by_user_id=row.created_by_user_id,
        email=row.email,
        created_at=row.created_at,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        accepted_by_user_id=row.accepted_by_user_id,
        revoked_at=row.revoked_at,
    )


def _to_audit(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        workspace_id=row.workspace_id,
        actor_user_id=row.actor_user_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        detail=dict(row.detail or {}),
        correlation_id=row.correlation_id,
        created_at=row.created_at,
    )


class PostgresTenancyStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_workspace(self, *, slug: str, name: str) -> Workspace:
        now = utcnow()
        workspace = Workspace(id=str(uuid7()), slug=slug, name=name, created_at=now, updated_at=now)
        try:
            async with session_scope(self._session_factory) as session:
                session.add(
                    WorkspaceRow(
                        id=workspace.id,
                        slug=workspace.slug,
                        name=workspace.name,
                        created_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as error:
            raise WorkspaceSlugConflict(f"workspace slug {slug!r} already exists") from error
        return workspace

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(select(WorkspaceRow).where(WorkspaceRow.id == workspace_id))
            ).scalar_one_or_none()
            return _to_workspace(row) if row else None

    async def get_workspace_by_slug(self, slug: str) -> Workspace | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(select(WorkspaceRow).where(WorkspaceRow.slug == slug))
            ).scalar_one_or_none()
            return _to_workspace(row) if row else None

    async def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace; memberships, projects, and invitations cascade.

        Audit events are intentionally retained as a security record even after
        the workspace is gone.
        """
        async with session_scope(self._session_factory) as session:
            result = await session.execute(
                delete(WorkspaceRow).where(WorkspaceRow.id == workspace_id)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def delete_project(self, workspace_id: str, project_id: str) -> bool:
        async with session_scope(self._session_factory) as session:
            result = await session.execute(
                delete(ProjectRow).where(
                    ProjectRow.id == project_id, ProjectRow.workspace_id == workspace_id
                )
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def list_workspaces_for_user(self, user_id: str) -> list[Workspace]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    select(WorkspaceRow)
                    .join(MembershipRow, MembershipRow.workspace_id == WorkspaceRow.id)
                    .where(MembershipRow.user_id == user_id)
                    .order_by(WorkspaceRow.created_at, WorkspaceRow.id)
                )
            ).scalars()
            return [_to_workspace(row) for row in rows]

    async def add_membership(self, *, workspace_id: str, user_id: str, role: Role) -> Membership:
        now = utcnow()
        membership = Membership(
            workspace_id=workspace_id, user_id=user_id, role=role, created_at=now, updated_at=now
        )
        try:
            async with session_scope(self._session_factory) as session:
                session.add(
                    MembershipRow(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        role=role.value,
                        created_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as error:
            raise MembershipConflict(
                f"user {user_id!r} is already a member of workspace {workspace_id!r}"
            ) from error
        return membership

    async def get_membership(self, workspace_id: str, user_id: str) -> Membership | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    select(MembershipRow).where(
                        MembershipRow.workspace_id == workspace_id,
                        MembershipRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            return _to_membership(row) if row else None

    async def list_memberships(self, workspace_id: str) -> list[Membership]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    select(MembershipRow)
                    .where(MembershipRow.workspace_id == workspace_id)
                    .order_by(MembershipRow.created_at, MembershipRow.user_id)
                )
            ).scalars()
            return [_to_membership(row) for row in rows]

    async def set_role(self, *, workspace_id: str, user_id: str, role: Role) -> Membership:
        async with session_scope(self._session_factory) as session:
            rows = list(
                (
                    await session.execute(
                        select(MembershipRow).where(MembershipRow.workspace_id == workspace_id)
                    )
                ).scalars()
            )
            target = next((row for row in rows if row.user_id == user_id), None)
            if target is None:
                raise MembershipConflict(f"user {user_id!r} is not a member of the workspace")
            ensure_role_change_allowed([_to_membership(row) for row in rows], user_id, role)
            target.role = role.value
            target.updated_at = utcnow()
            await session.flush()
            return _to_membership(target)

    async def remove_membership(self, *, workspace_id: str, user_id: str) -> bool:
        async with session_scope(self._session_factory) as session:
            rows = list(
                (
                    await session.execute(
                        select(MembershipRow).where(MembershipRow.workspace_id == workspace_id)
                    )
                ).scalars()
            )
            if not any(row.user_id == user_id for row in rows):
                return False
            ensure_member_removable([_to_membership(row) for row in rows], user_id)
            await session.execute(
                delete(MembershipRow).where(
                    MembershipRow.workspace_id == workspace_id,
                    MembershipRow.user_id == user_id,
                )
            )
            return True

    async def transfer_ownership(
        self, *, workspace_id: str, from_user_id: str, to_user_id: str
    ) -> None:
        async with session_scope(self._session_factory) as session:
            rows = list(
                (
                    await session.execute(
                        select(MembershipRow).where(MembershipRow.workspace_id == workspace_id)
                    )
                ).scalars()
            )
            ensure_ownership_transfer(
                [_to_membership(row) for row in rows], from_user_id, to_user_id
            )
            now = utcnow()
            for row in rows:
                if row.user_id == from_user_id:
                    row.role = Role.EDITOR.value
                    row.updated_at = now
                elif row.user_id == to_user_id:
                    row.role = Role.OWNER.value
                    row.updated_at = now
            await session.flush()

    async def create_project(self, *, workspace_id: str, slug: str, name: str) -> Project:
        now = utcnow()
        project = Project(
            id=str(uuid7()),
            workspace_id=workspace_id,
            slug=slug,
            name=name,
            created_at=now,
            updated_at=now,
        )
        try:
            async with session_scope(self._session_factory) as session:
                session.add(
                    ProjectRow(
                        id=project.id,
                        workspace_id=workspace_id,
                        slug=slug,
                        name=name,
                        created_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as error:
            raise ProjectSlugConflict(
                f"project slug {slug!r} already exists in workspace {workspace_id!r}"
            ) from error
        return project

    async def get_project(self, project_id: str) -> Project | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(select(ProjectRow).where(ProjectRow.id == project_id))
            ).scalar_one_or_none()
            return _to_project(row) if row else None

    async def list_projects(self, workspace_id: str) -> list[Project]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    select(ProjectRow)
                    .where(ProjectRow.workspace_id == workspace_id)
                    .order_by(ProjectRow.created_at, ProjectRow.id)
                )
            ).scalars()
            return [_to_project(row) for row in rows]

    async def create_invitation(
        self,
        *,
        workspace_id: str,
        role: Role,
        token_hash: str,
        created_by_user_id: str,
        ttl_seconds: int,
        email: str | None = None,
    ) -> Invitation:
        now = utcnow()
        invitation = Invitation(
            id=str(uuid7()),
            workspace_id=workspace_id,
            role=role,
            token_hash=token_hash,
            created_by_user_id=created_by_user_id,
            email=email,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        try:
            async with session_scope(self._session_factory) as session:
                session.add(
                    InvitationRow(
                        id=invitation.id,
                        workspace_id=workspace_id,
                        email=email,
                        role=role.value,
                        token_hash=token_hash,
                        created_by_user_id=created_by_user_id,
                        created_at=invitation.created_at,
                        expires_at=invitation.expires_at,
                    )
                )
        except IntegrityError as error:
            raise InvitationConflict("invitation token already exists") from error
        return invitation

    async def get_invitation_by_token_hash(self, token_hash: str) -> Invitation | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    select(InvitationRow).where(InvitationRow.token_hash == token_hash)
                )
            ).scalar_one_or_none()
            return _to_invitation(row) if row else None

    async def list_invitations(self, workspace_id: str) -> list[Invitation]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    select(InvitationRow)
                    .where(InvitationRow.workspace_id == workspace_id)
                    .order_by(InvitationRow.created_at, InvitationRow.id)
                )
            ).scalars()
            return [_to_invitation(row) for row in rows]

    async def revoke_invitation(self, *, workspace_id: str, invitation_id: str) -> bool:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    select(InvitationRow).where(
                        InvitationRow.id == invitation_id,
                        InvitationRow.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.accepted_at is not None or row.revoked_at is not None:
                return False
            row.revoked_at = utcnow()
            await session.flush()
            return True

    async def accept_invitation(self, *, token_hash: str, user_id: str) -> Membership:
        now = utcnow()
        async with session_scope(self._session_factory) as session:
            # Atomically claim the invitation: the conditional UPDATE re-evaluates
            # after a concurrent transaction releases the row lock, so two accepters
            # cannot both succeed.
            claimed = (
                await session.execute(
                    update(InvitationRow)
                    .where(
                        InvitationRow.token_hash == token_hash,
                        InvitationRow.accepted_at.is_(None),
                        InvitationRow.revoked_at.is_(None),
                        InvitationRow.expires_at > now,
                    )
                    .values(accepted_at=now, accepted_by_user_id=user_id)
                    .returning(InvitationRow)
                )
            ).scalar_one_or_none()
            if claimed is None:
                existing_row = (
                    await session.execute(
                        select(InvitationRow).where(InvitationRow.token_hash == token_hash)
                    )
                ).scalar_one_or_none()
                if existing_row is None:
                    raise InvitationNotFound("no invitation matches the supplied token")
                raise InvitationNotAcceptable(
                    f"invitation is {_to_invitation(existing_row).status(now).value} "
                    "and cannot be accepted"
                )
            row = claimed
            invitation = _to_invitation(row)

            inviter = (
                await session.execute(
                    select(MembershipRow).where(
                        MembershipRow.workspace_id == invitation.workspace_id,
                        MembershipRow.user_id == invitation.created_by_user_id,
                    )
                )
            ).scalar_one_or_none()
            if inviter is None or not role_allows(Role(inviter.role), Permission.MEMBER_INVITE):
                raise RoleTransitionError("inviter is no longer authorized to invite")

            existing = (
                await session.execute(
                    select(MembershipRow).where(
                        MembershipRow.workspace_id == invitation.workspace_id,
                        MembershipRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise MembershipConflict("user is already a member of the workspace")

            membership = Membership(
                workspace_id=invitation.workspace_id,
                user_id=user_id,
                role=invitation.role,
                created_at=now,
                updated_at=now,
            )
            session.add(
                MembershipRow(
                    workspace_id=membership.workspace_id,
                    user_id=user_id,
                    role=membership.role.value,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            return membership

    async def record_audit(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: dict | None = None,
        correlation_id: str = "",
    ) -> AuditEvent:
        now = utcnow()
        event = AuditEvent(
            id=str(uuid7()),
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=dict(detail or {}),
            correlation_id=correlation_id,
            created_at=now,
        )
        async with session_scope(self._session_factory) as session:
            session.add(
                AuditEventRow(
                    id=event.id,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    detail=event.detail,
                    correlation_id=correlation_id,
                    created_at=now,
                )
            )
        return event
