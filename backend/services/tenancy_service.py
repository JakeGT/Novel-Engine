"""Workspace/project/invitation application service.

Centralizes authorization: endpoints delegate here instead of duplicating role
checks. Every mutation records a security audit event containing identifiers and
outcomes only (never raw invitation or session tokens).
"""

from __future__ import annotations

import time

from backend.core.security import generate_token, hash_token
from backend.domain.errors import ForbiddenError, NotFoundError, RateLimitedError
from backend.domain.identity import AccessScope, Role
from backend.domain.tenancy import (
    Invitation,
    Membership,
    Permission,
    Project,
    Workspace,
    role_allows,
)
from backend.ports import (
    IdentityStorePort,
    RateLimiterPort,
    SessionStorePort,
    TenancyStorePort,
)

INVITATION_TTL_SECONDS = 60 * 60 * 24 * 7
ACCEPT_RATE_LIMIT = 10
ACCEPT_RATE_WINDOW_SECONDS = 60


class TenancyService:
    def __init__(
        self,
        store: TenancyStorePort,
        *,
        identity_store: IdentityStorePort | None = None,
        session_store: SessionStorePort | None = None,
        rate_limiter: RateLimiterPort | None = None,
        invitation_ttl_seconds: int = INVITATION_TTL_SECONDS,
        accept_rate_limit: int = ACCEPT_RATE_LIMIT,
        accept_rate_window_seconds: int = ACCEPT_RATE_WINDOW_SECONDS,
    ) -> None:
        self._store = store
        self._identity_store = identity_store
        self._session_store = session_store
        self._rate_limiter = rate_limiter
        self._invitation_ttl_seconds = invitation_ttl_seconds
        self._accept_rate_limit = accept_rate_limit
        self._accept_rate_window_seconds = accept_rate_window_seconds
        self._accept_hits: dict[str, list[float]] = {}

    async def _revoke_sessions(self, *user_ids: str) -> None:
        """Invalidate active sessions after a privilege change so roles re-evaluate."""
        if self._session_store is None:
            return
        for user_id in set(user_ids):
            await self._session_store.revoke_all_for_user(user_id)

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, permission: Permission
    ) -> Membership:
        membership = await self._store.get_membership(workspace_id, scope.actor_user_id)
        if membership is None or not role_allows(membership.role, permission):
            raise ForbiddenError("You do not have permission for this action.")
        return membership

    async def require_permission(
        self, scope: AccessScope, workspace_id: str, permission: Permission
    ) -> Membership:
        """Public authorization gate for other services (never reveals membership)."""
        return await self._authorize(scope, workspace_id, permission)

    async def _audit(
        self,
        scope: AccessScope,
        *,
        workspace_id: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: dict | None = None,
    ) -> None:
        await self._store.record_audit(
            workspace_id=workspace_id,
            actor_user_id=scope.actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            correlation_id=scope.correlation_id,
        )

    async def record_workspace_audit(
        self,
        scope: AccessScope,
        *,
        workspace_id: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: dict | None = None,
    ) -> None:
        """Public audit sink for other workspace-scoped services (e.g. credentials)."""
        await self._audit(
            scope,
            workspace_id=workspace_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )

    async def list_workspaces(self, scope: AccessScope) -> list[Workspace]:
        return await self._store.list_workspaces_for_user(scope.actor_user_id)

    async def require_membership(self, scope: AccessScope, workspace_id: str) -> Membership:
        """Assert the actor is a member of the workspace (any role); used to scope requests."""
        return await self._authorize(scope, workspace_id, Permission.WORKSPACE_READ)

    async def create_workspace(self, scope: AccessScope, *, slug: str, name: str) -> Workspace:
        workspace = await self._store.create_workspace(slug=slug, name=name)
        await self._store.add_membership(
            workspace_id=workspace.id, user_id=scope.actor_user_id, role=Role.OWNER
        )
        await self._audit(
            scope,
            workspace_id=workspace.id,
            action="workspace.created",
            target_type="workspace",
            target_id=workspace.id,
            detail={"slug": slug},
        )
        return workspace

    async def get_workspace(self, scope: AccessScope, workspace_id: str) -> Workspace:
        await self._authorize(scope, workspace_id, Permission.WORKSPACE_READ)
        workspace = await self._store.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found.")
        return workspace

    async def delete_workspace(self, scope: AccessScope, workspace_id: str) -> bool:
        await self._authorize(scope, workspace_id, Permission.WORKSPACE_ADMIN)
        existing = await self._store.get_workspace(workspace_id)
        if existing is None:
            raise NotFoundError("Workspace not found.")
        await self._audit(
            scope,
            workspace_id=workspace_id,
            action="workspace.deleted",
            target_type="workspace",
            target_id=workspace_id,
            detail={"slug": existing.slug},
        )
        return await self._store.delete_workspace(workspace_id)

    async def list_members(self, scope: AccessScope, workspace_id: str) -> list[Membership]:
        await self._authorize(scope, workspace_id, Permission.WORKSPACE_READ)
        return await self._store.list_memberships(workspace_id)

    async def set_role(
        self, scope: AccessScope, workspace_id: str, user_id: str, role: Role
    ) -> Membership:
        await self._authorize(scope, workspace_id, Permission.MEMBER_ROLE_CHANGE)
        membership = await self._store.set_role(
            workspace_id=workspace_id, user_id=user_id, role=role
        )
        await self._revoke_sessions(user_id)
        await self._audit(
            scope,
            workspace_id=workspace_id,
            action="membership.role_changed",
            target_type="membership",
            target_id=user_id,
            detail={"role": role.value},
        )
        return membership

    async def remove_member(self, scope: AccessScope, workspace_id: str, user_id: str) -> bool:
        await self._authorize(scope, workspace_id, Permission.MEMBER_REMOVE)
        removed = await self._store.remove_membership(workspace_id=workspace_id, user_id=user_id)
        if removed:
            await self._revoke_sessions(user_id)
            await self._audit(
                scope,
                workspace_id=workspace_id,
                action="membership.removed",
                target_type="membership",
                target_id=user_id,
            )
        return removed

    async def transfer_ownership(
        self, scope: AccessScope, workspace_id: str, to_user_id: str
    ) -> None:
        await self._authorize(scope, workspace_id, Permission.WORKSPACE_ADMIN)
        await self._store.transfer_ownership(
            workspace_id=workspace_id,
            from_user_id=scope.actor_user_id,
            to_user_id=to_user_id,
        )
        await self._revoke_sessions(to_user_id, scope.actor_user_id)
        await self._audit(
            scope,
            workspace_id=workspace_id,
            action="workspace.ownership_transferred",
            target_type="membership",
            target_id=to_user_id,
        )

    async def list_projects(self, scope: AccessScope, workspace_id: str) -> list[Project]:
        await self._authorize(scope, workspace_id, Permission.PROJECT_READ)
        return await self._store.list_projects(workspace_id)

    async def create_project(
        self, scope: AccessScope, workspace_id: str, *, slug: str, name: str
    ) -> Project:
        await self._authorize(scope, workspace_id, Permission.PROJECT_ADMIN)
        project = await self._store.create_project(workspace_id=workspace_id, slug=slug, name=name)
        await self._audit(
            scope,
            workspace_id=workspace_id,
            action="project.created",
            target_type="project",
            target_id=project.id,
            detail={"slug": slug},
        )
        return project

    async def create_invitation(
        self,
        scope: AccessScope,
        workspace_id: str,
        *,
        role: Role,
        email: str | None = None,
    ) -> tuple[Invitation, str]:
        """Return the persisted invitation and the one-time raw token."""
        await self._authorize(scope, workspace_id, Permission.MEMBER_INVITE)
        raw_token = generate_token()
        invitation = await self._store.create_invitation(
            workspace_id=workspace_id,
            role=role,
            token_hash=hash_token(raw_token),
            created_by_user_id=scope.actor_user_id,
            ttl_seconds=self._invitation_ttl_seconds,
            email=email,
        )
        await self._audit(
            scope,
            workspace_id=workspace_id,
            action="invitation.created",
            target_type="invitation",
            target_id=invitation.id,
            detail={"role": role.value, "email_bound": email is not None},
        )
        return invitation, raw_token

    async def list_invitations(self, scope: AccessScope, workspace_id: str) -> list[Invitation]:
        await self._authorize(scope, workspace_id, Permission.MEMBER_INVITE)
        return await self._store.list_invitations(workspace_id)

    async def revoke_invitation(
        self, scope: AccessScope, workspace_id: str, invitation_id: str
    ) -> bool:
        await self._authorize(scope, workspace_id, Permission.MEMBER_INVITE)
        revoked = await self._store.revoke_invitation(
            workspace_id=workspace_id, invitation_id=invitation_id
        )
        if revoked:
            await self._audit(
                scope,
                workspace_id=workspace_id,
                action="invitation.revoked",
                target_type="invitation",
                target_id=invitation_id,
            )
        return revoked

    async def _check_accept_rate(self, actor_user_id: str) -> None:
        if self._rate_limiter is not None:
            allowed = await self._rate_limiter.allow(
                f"invitation-accept:{actor_user_id}",
                limit=self._accept_rate_limit,
                window_seconds=self._accept_rate_window_seconds,
            )
            if not allowed:
                raise RateLimitedError("Too many invitation acceptance attempts; try again later.")
            return
        # Fallback in-memory limiter for single-worker deployments/tests without a shared limiter.
        now = time.monotonic()
        window = self._accept_rate_window_seconds
        hits = [hit for hit in self._accept_hits.get(actor_user_id, []) if now - hit < window]
        if len(hits) >= self._accept_rate_limit:
            raise RateLimitedError("Too many invitation acceptance attempts; try again later.")
        hits.append(now)
        self._accept_hits[actor_user_id] = hits

    async def accept_invitation(self, scope: AccessScope, *, token: str) -> Membership:
        await self._check_accept_rate(scope.actor_user_id)
        token_hash = hash_token(token)
        invitation = await self._store.get_invitation_by_token_hash(token_hash)
        if invitation is not None and invitation.email and self._identity_store is not None:
            user = await self._identity_store.get_user(scope.actor_user_id)
            if user is not None and user.email and user.email.lower() != invitation.email.lower():
                raise ForbiddenError("This invitation was issued to a different account.")
        membership = await self._store.accept_invitation(
            token_hash=token_hash, user_id=scope.actor_user_id
        )
        await self._audit(
            scope,
            workspace_id=membership.workspace_id,
            action="invitation.accepted",
            target_type="membership",
            target_id=scope.actor_user_id,
            detail={"role": membership.role.value},
        )
        return membership
