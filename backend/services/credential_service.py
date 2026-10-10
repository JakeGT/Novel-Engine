"""Credential vault application service.

Authorization is centralized: only workspace admins may manage credentials, and
only the authorized path resolves plaintext key material. Metadata is returned to
callers; the secret is never included in any response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.domain.credentials import Credential, CredentialProvider, CredentialStatus
from backend.domain.errors import CapabilityUnavailableError, ValidationError
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.providers import ProviderSpec, provider_spec
from backend.domain.tenancy import Permission
from backend.ports import CredentialVaultPort
from backend.services.tenancy_service import TenancyService

SUPPORTED_PROVIDERS = tuple(CredentialProvider)

MAX_MODEL_LENGTH = 200
# Named here because the service has a method called ``list``.
ModelNames = list[str]
# Provider model names: letters, digits and the punctuation providers use in them.
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")


@dataclass(frozen=True)
class ProviderRoute:
    """What the assistant will use for a workspace: which key, where, and which model."""

    credential_id: str
    api_key: str
    provider: ProviderSpec
    model: str


@dataclass(frozen=True)
class AiChoice:
    """The provider and model in use, without any key material."""

    provider: str | None
    model: str | None
    # True when an owner picked it; False when it follows from the only key connected.
    chosen: bool


class CredentialService:
    def __init__(
        self,
        vault: CredentialVaultPort,
        *,
        tenancy: TenancyService,
        ai_settings: Any = None,
        ai: Any = None,
        default_models: dict[str, str] | None = None,
    ) -> None:
        self._vault = vault
        self._tenancy = tenancy
        self._ai_settings = ai_settings
        self._ai = ai
        # Operator overrides of the suggested model per provider (e.g. from AI_MODEL).
        self._default_models = default_models or {}

    @property
    def is_configured(self) -> bool:
        return self._vault.is_configured

    async def _require_admin(self, scope: AccessScope, workspace_id: str) -> None:
        # Local mode is a trusted single-user deployment; hosted mode requires
        # workspace-admin membership. Managing credentials (create/rotate/revoke)
        # is an administrative action.
        if scope.auth_mode == AuthMode.LOCAL:
            return
        await self._tenancy.require_permission(scope, workspace_id, Permission.WORKSPACE_ADMIN)

    async def _require_use(self, scope: AccessScope, workspace_id: str) -> None:
        # Using an existing credential for inference only needs project-write
        # authority, so editors can run generation without workspace admin.
        if scope.auth_mode == AuthMode.LOCAL:
            return
        await self._tenancy.require_permission(scope, workspace_id, Permission.PROJECT_WRITE)

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
        await self._require_use(scope, workspace_id)
        return await self._vault.resolve_plaintext(
            workspace_id=workspace_id,
            credential_id=credential_id,
            actor_user_id=scope.actor_user_id,
        )

    async def mark_used(self, workspace_id: str, credential_id: str) -> None:
        await self._vault.mark_used(workspace_id=workspace_id, credential_id=credential_id)

    async def resolve_active_for_use(
        self, scope: AccessScope, workspace_id: str
    ) -> tuple[str, str] | None:
        """Return (credential_id, plaintext) for the workspace's active OpenRouter key.

        Requires project-write authority (editors may run generation); returns None
        when no active credential exists so the caller can decide the policy for
        that deployment.
        """
        # Authorize before inspecting vault metadata so callers cannot use the
        # empty/non-empty result as a workspace credential-existence oracle.
        await self._require_use(scope, workspace_id)
        credentials = await self._vault.list_for_workspace(workspace_id)
        active = [c for c in credentials if c.status == CredentialStatus.ACTIVE]
        if not active:
            return None
        # Defensive: choose the most recently created active key. The create path
        # already enforces a single active credential per (workspace, provider).
        credential = max(active, key=lambda c: c.created_at)
        plaintext = await self._vault.resolve_plaintext(
            workspace_id=workspace_id,
            credential_id=credential.id,
            actor_user_id=scope.actor_user_id,
        )
        return credential.id, plaintext

    # --- which provider and model the assistant uses --------------------------------

    async def _active_by_provider(self, workspace_id: str) -> dict[str, Credential]:
        active: dict[str, Credential] = {}
        for credential in await self._vault.list_for_workspace(workspace_id):
            if credential.status != CredentialStatus.ACTIVE:
                continue
            current = active.get(credential.provider.value)
            if current is None or credential.created_at > current.created_at:
                active[credential.provider.value] = credential
        return active

    def _suggested_model(self, provider: str) -> str | None:
        spec = provider_spec(provider)
        return self._default_models.get(provider) or (spec.default_model if spec else None)

    async def _choice(self, workspace_id: str) -> tuple[AiChoice, dict[str, Credential]]:
        active = await self._active_by_provider(workspace_id)
        stored = await self._ai_settings.get(workspace_id) if self._ai_settings else None
        if stored is not None and stored[0] in active:
            return AiChoice(provider=stored[0], model=stored[1], chosen=True), active
        if not active:
            return AiChoice(provider=None, model=None, chosen=False), active
        # No choice made (or its key was removed): follow the newest key connected.
        newest = max(active.values(), key=lambda credential: credential.created_at)
        provider = newest.provider.value
        return AiChoice(
            provider=provider, model=self._suggested_model(provider), chosen=False
        ), active

    async def ai_choice(self, scope: AccessScope, workspace_id: str) -> AiChoice:
        """Which provider and model are in use. Any member who can write may see it."""
        await self._require_use(scope, workspace_id)
        return (await self._choice(workspace_id))[0]

    async def choose_ai(
        self, scope: AccessScope, workspace_id: str, *, provider: str, model: str
    ) -> AiChoice:
        await self._require_admin(scope, workspace_id)
        if self._ai_settings is None:
            raise CapabilityUnavailableError("Model choice is not available on this site.")
        model = model.strip()
        if provider_spec(provider) is None:
            raise ValidationError("Unknown AI provider.")
        if not model or len(model) > MAX_MODEL_LENGTH or not _MODEL_NAME.match(model):
            raise ValidationError("That does not look like a model name.")
        if provider not in await self._active_by_provider(workspace_id):
            raise ValidationError("Connect a key for that provider first.")
        await self._ai_settings.set(
            workspace_id=workspace_id,
            provider=provider,
            model=model,
            actor_user_id=scope.actor_user_id,
        )
        await self._tenancy.record_workspace_audit(
            scope,
            workspace_id=workspace_id,
            action="ai.model_chosen",
            target_type="workspace",
            target_id=workspace_id,
            detail={"provider": provider, "model": model},
        )
        return AiChoice(provider=provider, model=model, chosen=True)

    async def list_models(self, scope: AccessScope, workspace_id: str, provider: str) -> ModelNames:
        """Ask the provider which models the workspace's key can use."""
        await self._require_admin(scope, workspace_id)
        spec = provider_spec(provider)
        if spec is None:
            raise ValidationError("Unknown AI provider.")
        credential = (await self._active_by_provider(workspace_id)).get(provider)
        if credential is None or self._ai is None:
            raise ValidationError("Connect a key for that provider first.")
        api_key = await self._vault.resolve_plaintext(
            workspace_id=workspace_id,
            credential_id=credential.id,
            actor_user_id=scope.actor_user_id,
        )
        models: ModelNames = await self._ai.list_models(api_key=api_key, base_url=spec.base_url)
        return models

    async def resolve_route_for_use(
        self, scope: AccessScope, workspace_id: str
    ) -> ProviderRoute | None:
        """The key, address and model for an assistant call; None when no key is connected.

        Requires project-write authority (editors may run the assistant). Authorizes
        before looking at the vault, so the answer is not a key-existence oracle.
        """
        await self._require_use(scope, workspace_id)
        choice, active = await self._choice(workspace_id)
        if choice.provider is None:
            return None
        spec = provider_spec(choice.provider)
        if spec is None:
            return None
        if not choice.model:
            raise CapabilityUnavailableError(
                "No model has been chosen for this workspace's AI provider."
            )
        credential = active[choice.provider]
        plaintext = await self._vault.resolve_plaintext(
            workspace_id=workspace_id,
            credential_id=credential.id,
            actor_user_id=scope.actor_user_id,
        )
        return ProviderRoute(
            credential_id=credential.id, api_key=plaintext, provider=spec, model=choice.model
        )
