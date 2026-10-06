"""Workspace, membership, project, invitation, and audit domain models.

Provider-neutral and persistence-neutral: no ORM, OIDC, or driver types leak in.
Roles map to an explicit permission matrix, and ownership transitions are
validated by pure functions so both adapters and services share one policy.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from backend.domain.identity import Role, utcnow


class Permission(StrEnum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_ADMIN = "workspace.admin"
    MEMBER_INVITE = "member.invite"
    MEMBER_REMOVE = "member.remove"
    MEMBER_ROLE_CHANGE = "member.role_change"
    PROJECT_READ = "project.read"
    PROJECT_WRITE = "project.write"
    PROJECT_ADMIN = "project.admin"
    AUDIT_READ = "audit.read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.EDITOR: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.PROJECT_WRITE,
        }
    ),
    Role.VIEWER: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
        }
    ),
}


def role_allows(role: Role, permission: Permission) -> bool:
    """Return whether ``role`` grants ``permission``. Unknown roles grant nothing."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


class Workspace(BaseModel):
    id: str
    slug: str
    name: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Membership(BaseModel):
    workspace_id: str
    user_id: str
    role: Role
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Project(BaseModel):
    id: str
    workspace_id: str
    slug: str
    name: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class Invitation(BaseModel):
    id: str
    workspace_id: str
    role: Role
    token_hash: str
    created_by_user_id: str
    email: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by_user_id: str | None = None
    revoked_at: datetime | None = None

    def status(self, now: datetime | None = None) -> InvitationStatus:
        moment = now or utcnow()
        if self.revoked_at is not None:
            return InvitationStatus.REVOKED
        if self.accepted_at is not None:
            return InvitationStatus.ACCEPTED
        if self.expires_at <= moment:
            return InvitationStatus.EXPIRED
        return InvitationStatus.PENDING

    def is_acceptable(self, now: datetime | None = None) -> bool:
        return self.status(now) == InvitationStatus.PENDING


class AuditEvent(BaseModel):
    id: str
    workspace_id: str
    actor_user_id: str
    action: str
    target_type: str
    target_id: str
    detail: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class TenancyError(RuntimeError):
    """Base class for tenancy/policy failures."""


class WorkspaceSlugConflict(TenancyError):
    """A workspace with this slug already exists."""


class ProjectSlugConflict(TenancyError):
    """A project with this slug already exists in the workspace."""


class MembershipConflict(TenancyError):
    """The user is already a member of the workspace."""


class InvitationConflict(TenancyError):
    """An equivalent invitation already exists."""


class InvitationNotFound(TenancyError):
    """No invitation matches the supplied token."""


class InvitationNotAcceptable(TenancyError):
    """The invitation is expired, revoked, or already accepted."""


class LastOwnerRemoval(TenancyError):
    """A transition would leave the workspace without an owner."""


class RoleTransitionError(TenancyError):
    """The requested role/ownership transition is not permitted."""


def _owners(memberships: Iterable[Membership]) -> list[Membership]:
    return [membership for membership in memberships if membership.role == Role.OWNER]


def is_last_owner(memberships: Iterable[Membership], user_id: str) -> bool:
    owners = _owners(memberships)
    return len(owners) == 1 and owners[0].user_id == user_id


def ensure_member_removable(memberships: Iterable[Membership], target_user_id: str) -> None:
    if is_last_owner(memberships, target_user_id):
        raise LastOwnerRemoval("cannot remove the last owner of a workspace")


def ensure_role_change_allowed(
    memberships: Iterable[Membership], target_user_id: str, new_role: Role
) -> None:
    if new_role != Role.OWNER and is_last_owner(memberships, target_user_id):
        raise LastOwnerRemoval("cannot demote the last owner of a workspace")


def ensure_ownership_transfer(
    memberships: Iterable[Membership], from_user_id: str, to_user_id: str
) -> None:
    materialized = list(memberships)
    if from_user_id == to_user_id:
        raise RoleTransitionError("cannot transfer ownership to the current owner")
    if not any(m.user_id == from_user_id and m.role == Role.OWNER for m in materialized):
        raise RoleTransitionError("only an owner can transfer ownership")
    if not any(m.user_id == to_user_id for m in materialized):
        raise RoleTransitionError("ownership can only be transferred to a member")
