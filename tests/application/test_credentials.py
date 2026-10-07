import pytest

from backend.domain.credentials import (
    CredentialNotFound,
    CredentialProvider,
    CredentialStatus,
)
from backend.domain.errors import ForbiddenError
from backend.domain.identity import AccessScope, AuthMode, IdentityClaims, Role
from backend.services.credential_service import CredentialService
from backend.services.tenancy_service import TenancyService
from tests.doubles import InMemoryCredentialVault, InMemoryIdentityStore, InMemoryTenancyStore

pytestmark = pytest.mark.application


async def _setup() -> tuple[CredentialService, AccessScope, AccessScope, str]:
    tenancy = InMemoryTenancyStore()
    identity = InMemoryIdentityStore()
    tenancy_service = TenancyService(tenancy, identity_store=identity)
    owner = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="owner"))
    editor = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="editor"))
    owner_scope = AccessScope(actor_user_id=owner.id, auth_mode=AuthMode.HOSTED)
    editor_scope = AccessScope(actor_user_id=editor.id, auth_mode=AuthMode.HOSTED)
    workspace = await tenancy_service.create_workspace(owner_scope, slug="ws", name="WS")
    _invitation, token = await tenancy_service.create_invitation(
        owner_scope, workspace.id, role=Role.EDITOR
    )
    await tenancy_service.accept_invitation(editor_scope, token=token)
    service = CredentialService(InMemoryCredentialVault(), tenancy=tenancy_service)
    return service, owner_scope, editor_scope, workspace.id


@pytest.mark.asyncio
async def test_lifecycle_never_exposes_secret() -> None:
    service, owner_scope, _editor_scope, workspace_id = await _setup()

    credential = await service.create(
        owner_scope,
        workspace_id=workspace_id,
        provider=CredentialProvider.OPENROUTER,
        label="mine",
        plaintext_key="sk-or-v1-supersecret",
    )

    assert credential.key_last4 == "cret"
    assert "supersecret" not in credential.model_dump_json()
    assert "sk-or-" not in credential.model_dump_json()

    assert [c.id for c in await service.list(owner_scope, workspace_id)] == [credential.id]
    assert (
        await service.resolve_for_use(owner_scope, workspace_id, credential.id)
        == "sk-or-v1-supersecret"
    )

    rotated = await service.rotate(
        owner_scope, workspace_id, credential.id, "sk-or-v1-rotated-9999"
    )
    assert rotated.key_last4 == "9999"

    revoked = await service.revoke(owner_scope, workspace_id, credential.id)
    assert revoked.status == CredentialStatus.REVOKED
    with pytest.raises(CredentialNotFound):
        await service.resolve_for_use(owner_scope, workspace_id, credential.id)

    assert await service.delete(owner_scope, workspace_id, credential.id) is True
    assert await service.list(owner_scope, workspace_id) == []


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_credentials() -> None:
    service, _owner_scope, editor_scope, workspace_id = await _setup()

    with pytest.raises(ForbiddenError):
        await service.create(
            editor_scope,
            workspace_id=workspace_id,
            provider=CredentialProvider.OPENROUTER,
            label="nope",
            plaintext_key="sk-or-v1-aaaaaaaa",
        )
    with pytest.raises(ForbiddenError):
        await service.list(editor_scope, workspace_id)


@pytest.mark.asyncio
async def test_local_mode_is_trusted_without_membership() -> None:
    tenancy = InMemoryTenancyStore()
    identity = InMemoryIdentityStore()
    service = CredentialService(
        InMemoryCredentialVault(),
        tenancy=TenancyService(tenancy, identity_store=identity),
    )
    local = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="local-user"))
    scope = AccessScope(actor_user_id=local.id, auth_mode=AuthMode.LOCAL)

    credential = await service.create(
        scope,
        workspace_id="local-workspace",
        provider=CredentialProvider.OPENROUTER,
        label="local",
        plaintext_key="sk-or-v1-local-key",
    )
    assert credential.workspace_id == "local-workspace"


@pytest.mark.asyncio
async def test_credential_actions_are_audited() -> None:
    tenancy = InMemoryTenancyStore()
    identity = InMemoryIdentityStore()
    tenancy_service = TenancyService(tenancy, identity_store=identity)
    owner = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="owner"))
    scope = AccessScope(actor_user_id=owner.id, auth_mode=AuthMode.HOSTED)
    workspace = await tenancy_service.create_workspace(scope, slug="ws", name="WS")
    service = CredentialService(InMemoryCredentialVault(), tenancy=tenancy_service)

    credential = await service.create(
        scope,
        workspace_id=workspace.id,
        provider=CredentialProvider.OPENROUTER,
        label="mine",
        plaintext_key="sk-or-v1-aaaaaaaa",
    )
    await service.revoke(scope, workspace.id, credential.id)

    actions = [event.action for event in tenancy.audit]
    assert "credential.created" in actions
    assert "credential.revoked" in actions
    # Audit detail never contains the secret.
    assert all("sk-or-" not in str(event.detail) for event in tenancy.audit)


@pytest.mark.asyncio
async def test_editor_can_resolve_credential_but_not_manage_it() -> None:
    service, owner_scope, editor_scope, workspace_id = await _setup()
    await service.create(
        owner_scope,
        workspace_id=workspace_id,
        provider=CredentialProvider.OPENROUTER,
        label="shared",
        plaintext_key="sk-or-v1-sharedkey123",
    )

    # Editors (PROJECT_WRITE) may use the workspace key for inference...
    resolved = await service.resolve_active_for_use(editor_scope, workspace_id)
    assert resolved is not None
    assert resolved[1] == "sk-or-v1-sharedkey123"

    # ...but only admins may manage credentials.
    with pytest.raises(ForbiddenError):
        await service.create(
            editor_scope,
            workspace_id=workspace_id,
            provider=CredentialProvider.OPENROUTER,
            label="editor-owned",
            plaintext_key="sk-or-v1-editor",
        )


@pytest.mark.asyncio
async def test_only_one_active_credential_per_provider() -> None:
    service, owner_scope, _editor_scope, workspace_id = await _setup()

    first = await service.create(
        owner_scope,
        workspace_id=workspace_id,
        provider=CredentialProvider.OPENROUTER,
        label="one",
        plaintext_key="sk-or-v1-first",
    )
    second = await service.create(
        owner_scope,
        workspace_id=workspace_id,
        provider=CredentialProvider.OPENROUTER,
        label="two",
        plaintext_key="sk-or-v1-second",
    )

    statuses = {c.id: c.status for c in await service.list(owner_scope, workspace_id)}
    assert statuses[first.id] == CredentialStatus.REVOKED
    assert statuses[second.id] == CredentialStatus.ACTIVE

    resolved = await service.resolve_active_for_use(owner_scope, workspace_id)
    assert resolved is not None
    assert resolved[0] == second.id
    assert resolved[1] == "sk-or-v1-second"
