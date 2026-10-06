"""Credential vault application service.

Authorization is centralized: only workspace admins may manage credentials, and
only the authorized path resolves plaintext key material. Metadata is returned to
callers; the secret is never included in any response.
"""

from __future__ import annotations

from backend.domain.credentials import Credential, CredentialProvider
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.tenancy import Permission
from backend.ports import CredentialVaultPort
from backend.services.tenancy_service import TenancyService

SUPPORTED_PROVIDERS = (CredentialProvider.OPENROUTER,)


class CredentialService:
    def __init__(self, vault: CredentialVaultPort, *, tenancy: TenancyService) -> None:
        self._vault = vault
        self._tenancy = tenancy

    @property
    def is_configured(self) -> bool:
        return self._vault.is_configured

    async def _require_admin(self, scope: AccessScope, workspace_id: str) -> None:
        # Local mode is a trusted single-user deployment; hosted mode requires
        # workspace-admin membership.
        if scope.auth_mode == AuthMode.LOCAL:
            return
        await self._tenancy.require_permission(scope, workspace_id, Permission.WORKSPACE_ADMIN)

    async def create(
        self,
        scope: AccessScope,
        *,
        workspace_id: str,
        provider: CredentialProvider,
        label: str,
        plaintext_key: str,
    ) -> Credential:
        await self._require_admin(scope, workspace_id)
        credential = await self._vault.store(
            workspace_id=workspace_id,
            owner_user_id=scope.actor_user_id,
            provider=provider,
            label=label,
            plaintext_key=plaintext_key,
        )
        await self._tenancy.record_workspace_audit(
            scope,
            workspace_id=workspace_id,
            action="credential.created",
            target_type="credential",
            target_id=credential.id,
            detail={"provider": provider.value},
        )
        return credential

    async def list(self, scope: AccessScope, workspace_id: str) -> list[Credential]:
        await self._require_admin(scope, workspace_id)
        return await self._vault.list_for_workspace(workspace_id)

    async def rotate(
        self, scope: AccessScope, workspace_id: str, credential_id: str, plaintext_key: str
    ) -> Credential:
        await self._require_admin(scope, workspace_id)
        credential = await self._vault.rotate(
            workspace_id=workspace_id, credential_id=credential_id, plaintext_key=plaintext_key
        )
        await self._tenancy.record_workspace_audit(
            scope,
            workspace_id=workspace_id,
            action="credential.rotated",
            target_type="credential",
            target_id=credential_id,
        )
        return credential

    async def revoke(self, scope: AccessScope, workspace_id: str, credential_id: str) -> Credential:
        await self._require_admin(scope, workspace_id)
        credential = await self._vault.revoke(
            workspace_id=workspace_id, credential_id=credential_id
        )
        await self._tenancy.record_workspace_audit(
            scope,
            workspace_id=workspace_id,
            action="credential.revoked",
            target_type="credential",
            target_id=credential_id,
        )
        return credential

    async def delete(self, scope: AccessScope, workspace_id: str, credential_id: str) -> bool:
        await self._require_admin(scope, workspace_id)
        deleted = await self._vault.delete(workspace_id=workspace_id, credential_id=credential_id)
        if deleted:
            await self._tenancy.record_workspace_audit(
                scope,
                workspace_id=workspace_id,
                action="credential.deleted",
                target_type="credential",
                target_id=credential_id,
            )
        return deleted

    async def resolve_for_use(
        self, scope: AccessScope, workspace_id: str, credential_id: str
    ) -> str:
        """Worker-only path: return plaintext key material for immediate provider use."""
        await self._require_admin(scope, workspace_id)
        return await self._vault.resolve_plaintext(
            workspace_id=workspace_id,
            credential_id=credential_id,
            actor_user_id=scope.actor_user_id,
        )

    async def mark_used(self, workspace_id: str, credential_id: str) -> None:
        await self._vault.mark_used(workspace_id=workspace_id, credential_id=credential_id)
