"""Personal access keys for other assistants (QRY-003, ADR-024).

A key belongs to one member and one workspace, and reads one story or every story
its holder can read there. It is checked on every request, never cached: an expired
or withdrawn key, a closed account, or a member who lost access to what the key
reads is refused, and in the last two cases the key is withdrawn for good. What a
key may do is never more than its holder may do at that moment: every tool goes
through the same application services as the web app, with the holder's identity.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.core.config import Settings
from backend.core.security import hash_token
from backend.domain.access_keys import (
    DEFAULT_CALLS_PER_MINUTE,
    FAILED_ATTEMPTS_PER_MINUTE,
    MAX_ACTIVE_KEYS_PER_MEMBER,
    AccessKey,
    AccessKeyStatus,
    AccessKeyUse,
    RevokedBecause,
    generate_key,
    key_hint,
    lifetime,
    well_formed,
)
from backend.domain.errors import (
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from backend.domain.identity import AccessScope, AuthMode, Role, utcnow
from backend.domain.tenancy import Permission, role_allows
from backend.ports import AccessKeyStorePort, IdentityStorePort, RateLimiterPort
from backend.services.tenancy_service import TenancyService

USE_DAYS = 30


@dataclass(frozen=True)
class MachineCaller:
    """Who is calling through a key, checked just now: the key and its holder's role."""

    key: AccessKey
    role: Role
    auth_mode: AuthMode

    def scope(self, project_id: str | None, correlation_id: str = "") -> AccessScope:
        """The holder's own scope: the same one the web app would use for them."""
        return AccessScope(
            actor_user_id=self.key.user_id,
            auth_mode=self.auth_mode,
            correlation_id=correlation_id,
            workspace_id=self.key.workspace_id,
            project_id=project_id,
        )

    @property
    def can_suggest(self) -> bool:
        """Allowed to suggest, and its holder may still change the story."""
        return self.key.may_suggest and role_allows(self.role, Permission.PROJECT_WRITE)


@dataclass(frozen=True)
class ListedKey:
    key: AccessKey
    mine: bool
    holder_name: str | None


class AccessKeyService:
    def __init__(
        self,
        store: AccessKeyStorePort,
        *,
        tenancy: TenancyService,
        identity_store: IdentityStorePort,
        rate_limiter: RateLimiterPort,
        settings: Settings,
        calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE,
        failed_per_minute: int = FAILED_ATTEMPTS_PER_MINUTE,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._store = store
        self._tenancy = tenancy
        self._identities = identity_store
        self._limiter = rate_limiter
        self._settings = settings
        self._calls_per_minute = calls_per_minute
        self._failed_per_minute = failed_per_minute
        self._now = now
        # Removing someone from a workspace withdraws their keys there at once (ADR-024).
        tenancy.attach_member_keys(self)

    @property
    def _local(self) -> bool:
        return not self._settings.is_hosted

    # ------------------------------------------------------------------ for members

    async def _role(self, scope: AccessScope, workspace_id: str) -> Role:
        if self._local:
            if workspace_id != self._settings.AUTH_LOCAL_WORKSPACE_ID:
                raise NotFoundError("Workspace not found.")
            return Role.OWNER
        membership = await self._tenancy.require_membership(scope, workspace_id)
        return membership.role

    async def create(
        self,
        scope: AccessScope,
        workspace_id: str,
        *,
        name: str,
        project_id: str | None,
        may_suggest: bool,
        lifetime_days: int | None,
    ) -> tuple[AccessKey, str]:
        """Make a key; the key itself is returned only here and never stored."""
        role = await self._role(scope, workspace_id)
        label = name.strip()
        if not label or len(label) > 100:
            raise ValidationError(
                "Give the key a name of up to 100 characters.", field_errors={"name": "required"}
            )
        if project_id is not None:
            if self._local:
                if project_id != self._settings.AUTH_LOCAL_PROJECT_ID:
                    raise NotFoundError("Project not found.")
            else:
                await self._tenancy.require_project(
                    scope, workspace_id, project_id, Permission.PROJECT_READ
                )
        if may_suggest and not role_allows(role, Permission.PROJECT_WRITE):
            raise ForbiddenError(
                "Only someone who can change the story can let a key suggest changes."
            )
        try:
            duration = lifetime(lifetime_days)
        except ValueError as error:
            raise ValidationError(
                str(error), field_errors={"expires_in_days": str(error)}
            ) from None
        now = self._now()
        mine = await self._store.list_keys(workspace_id=workspace_id, user_id=scope.actor_user_id)
        if sum(1 for key in mine if key.status(now) is AccessKeyStatus.ACTIVE) >= (
            MAX_ACTIVE_KEYS_PER_MEMBER
        ):
            raise ValidationError(
                f"You already have {MAX_ACTIVE_KEYS_PER_MEMBER} keys that work. "
                "Withdraw one you no longer use first."
            )
        raw = generate_key()
        key = AccessKey(
            id=f"key_{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            user_id=scope.actor_user_id,
            project_id=project_id,
            name=label,
            hint=key_hint(raw),
            may_suggest=may_suggest,
            token_hash=hash_token(raw),
            created_at=now,
            expires_at=now + duration,
        )
        await self._store.create(key)
        await self._tenancy.record_workspace_audit(
            scope,
            workspace_id=workspace_id,
            action="access_key.created",
            target_type="access_key",
            target_id=key.id,
            detail={
                "stories": "one" if project_id else "all",
                "may_suggest": may_suggest,
                "days": duration.days,
            },
        )
        return key, raw

    async def list_keys(self, scope: AccessScope, workspace_id: str) -> list[ListedKey]:
        """Your own keys; an owner also sees everyone else's, to withdraw one if needed."""
        role = await self._role(scope, workspace_id)
        everyone = not self._local and role_allows(role, Permission.MEMBER_REMOVE)
        keys = await self._store.list_keys(
            workspace_id=workspace_id, user_id=None if everyone else scope.actor_user_id
        )
        names: dict[str, str | None] = {}
        listed: list[ListedKey] = []
        for key in keys:
            mine = key.user_id == scope.actor_user_id
            if not mine and key.user_id not in names:
                user = await self._identities.get_user(key.user_id)
                names[key.user_id] = (user.display_name or user.email) if user else None
            listed.append(ListedKey(key=key, mine=mine, holder_name=names.get(key.user_id)))
        return listed

    async def _visible(self, scope: AccessScope, workspace_id: str, key_id: str) -> AccessKey:
        role = await self._role(scope, workspace_id)
        key = await self._store.get(workspace_id=workspace_id, key_id=key_id)
        if key is None:
            raise NotFoundError("That key was not found.")
        if key.user_id != scope.actor_user_id and (
            self._local or not role_allows(role, Permission.MEMBER_REMOVE)
        ):
            # Someone else's key is not there at all for anyone but an owner.
            raise NotFoundError("That key was not found.")
        return key

    async def revoke(self, scope: AccessScope, workspace_id: str, key_id: str) -> AccessKey:
        key = await self._visible(scope, workspace_id, key_id)
        reason = (
            RevokedBecause.HOLDER if key.user_id == scope.actor_user_id else RevokedBecause.OWNER
        )
        if await self._store.revoke(key_id=key.id, reason=reason.value, at=self._now()):
            await self._tenancy.record_workspace_audit(
                scope,
                workspace_id=workspace_id,
                action="access_key.revoked",
                target_type="access_key",
                target_id=key.id,
                detail={"by": reason.value},
            )
        refreshed = await self._store.get(workspace_id=workspace_id, key_id=key.id)
        assert refreshed is not None
        return refreshed

    async def withdraw_for_member(
        self, scope: AccessScope, workspace_id: str, user_id: str
    ) -> list[str]:
        """Withdraw for good every working key a member holds here: they lost access.

        Called when they are removed, so coming back later never brings a key back.
        """
        withdrawn = await self._store.revoke_for_member(
            workspace_id=workspace_id,
            user_id=user_id,
            reason=RevokedBecause.ACCESS_LOST.value,
            at=self._now(),
        )
        for key_id in withdrawn:
            await self._tenancy.record_workspace_audit(
                scope,
                workspace_id=workspace_id,
                action="access_key.revoked",
                target_type="access_key",
                target_id=key_id,
                detail={"by": RevokedBecause.ACCESS_LOST.value},
            )
        return withdrawn

    async def uses(self, scope: AccessScope, workspace_id: str, key_id: str) -> list[AccessKeyUse]:
        """How often the key called each tool over the last 30 days: counts only."""
        key = await self._visible(scope, workspace_id, key_id)
        since = (self._now() - timedelta(days=USE_DAYS - 1)).date().isoformat()
        return await self._store.uses(key_id=key.id, since_day=since)

    # ------------------------------------------------------------------ for machine clients

    async def _refuse_unknown(self, client: str) -> None:
        if not await self._limiter.allow(
            f"access-key-failed:{client}", limit=self._failed_per_minute, window_seconds=60
        ):
            raise RateLimitedError("Too many attempts with keys that do not work. Wait a minute.")

    async def _withdraw(self, key: AccessKey, reason: RevokedBecause) -> None:
        if await self._store.revoke(key_id=key.id, reason=reason.value, at=self._now()):
            await self._tenancy.record_workspace_audit(
                AccessScope(actor_user_id=key.user_id, auth_mode=self._mode),
                workspace_id=key.workspace_id,
                action="access_key.revoked",
                target_type="access_key",
                target_id=key.id,
                detail={"by": reason.value},
            )

    @property
    def _mode(self) -> AuthMode:
        return AuthMode.LOCAL if self._local else AuthMode.HOSTED

    async def authenticate(self, presented: str | None, *, client: str) -> MachineCaller:
        """Check a presented key now, against current access. Raises when it does not work."""
        if not presented or not well_formed(presented):
            await self._refuse_unknown(client)
            raise UnauthorizedError("An access key from Novel Engine is required.")
        digest = hash_token(presented)
        key = await self._store.get_by_hash(digest)
        if key is None or not hmac.compare_digest(key.token_hash, digest):
            await self._refuse_unknown(client)
            raise UnauthorizedError("That access key does not work.")
        status = key.status(self._now())
        if status is not AccessKeyStatus.ACTIVE:
            # A stopped key is still a failed attempt: it counts against the address.
            await self._refuse_unknown(client)
        if status is AccessKeyStatus.REVOKED:
            raise UnauthorizedError("That access key was withdrawn.")
        if status is AccessKeyStatus.EXPIRED:
            raise UnauthorizedError("That access key has expired. Make a new one in People.")
        user = await self._identities.get_user(key.user_id)
        if user is None or not user.is_active:
            await self._withdraw(key, RevokedBecause.ACCOUNT_CLOSED)
            raise UnauthorizedError("That access key was withdrawn.")
        role = Role.OWNER
        if not self._local:
            holder = AccessScope(actor_user_id=key.user_id, auth_mode=self._mode)
            try:
                if key.project_id is not None:
                    await self._tenancy.require_project(
                        holder, key.workspace_id, key.project_id, Permission.PROJECT_READ
                    )
                role = (await self._tenancy.require_membership(holder, key.workspace_id)).role
            except (ForbiddenError, NotFoundError):
                await self._withdraw(key, RevokedBecause.ACCESS_LOST)
                raise UnauthorizedError(
                    "That access key was withdrawn: its holder no longer has access to it."
                ) from None
        if not await self._limiter.allow(
            f"access-key:{key.id}", limit=self._calls_per_minute, window_seconds=60
        ):
            raise RateLimitedError("Too many requests with this key. Wait a minute.")
        await self._store.touch(key_id=key.id, at=self._now())
        return MachineCaller(key=key, role=role, auth_mode=self._mode)

    async def record_use(self, caller: MachineCaller, tool: str, *, refused: bool) -> None:
        """Count one tool call. Numbers and the tool's name only, never what it read."""
        await self._store.record_use(
            key_id=caller.key.id,
            workspace_id=caller.key.workspace_id,
            day=self._now().date().isoformat(),
            tool=tool,
            refused=refused,
        )


__all__ = ["AccessKeyService", "ListedKey", "MachineCaller"]
