import pytest

from backend.domain.errors import ForbiddenError
from backend.domain.identity import AccessScope, AuthMode, IdentityClaims, Role
from backend.services.tenancy_service import TenancyService
from tests.doubles import InMemoryIdentityStore, InMemoryRateLimiter, InMemoryTenancyStore

pytestmark = pytest.mark.application


async def _service():
    identity = InMemoryIdentityStore()
    service = TenancyService(
        InMemoryTenancyStore(),
        identity_store=identity,
        rate_limiter=InMemoryRateLimiter(),
    )
    return service, identity


@pytest.mark.asyncio
async def test_email_bound_invitation_rejects_account_without_email() -> None:
    service, identity = await _service()
    owner = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="owner"))
    no_email = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="no-email"))
    owner_scope = AccessScope(actor_user_id=owner.id, auth_mode=AuthMode.LOCAL)
    member_scope = AccessScope(actor_user_id=no_email.id, auth_mode=AuthMode.LOCAL)

    workspace = await service.create_workspace(owner_scope, slug="bound", name="Bound")
    _invitation, token = await service.create_invitation(
        owner_scope, workspace.id, role=Role.EDITOR, email="bound@example.com"
    )

    with pytest.raises(ForbiddenError):
        await service.accept_invitation(member_scope, token=token)


@pytest.mark.asyncio
async def test_email_bound_invitation_accepts_matching_account() -> None:
    service, identity = await _service()
    owner = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="owner"))
    matching = await identity.get_or_create_user(
        IdentityClaims(issuer="local", subject="matching", email="bound@example.com")
    )
    owner_scope = AccessScope(actor_user_id=owner.id, auth_mode=AuthMode.LOCAL)
    member_scope = AccessScope(actor_user_id=matching.id, auth_mode=AuthMode.LOCAL)

    workspace = await service.create_workspace(owner_scope, slug="bound2", name="Bound2")
    _invitation, token = await service.create_invitation(
        owner_scope, workspace.id, role=Role.EDITOR, email="Bound@Example.com"
    )

    membership = await service.accept_invitation(member_scope, token=token)
    assert membership.workspace_id == workspace.id
