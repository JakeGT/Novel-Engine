import pytest

from backend.domain.errors import RateLimitedError
from backend.domain.identity import AccessScope, AuthMode, IdentityClaims, Role
from backend.services.tenancy_service import TenancyService
from tests.doubles import InMemoryIdentityStore, InMemoryRateLimiter, InMemoryTenancyStore

pytestmark = pytest.mark.application


@pytest.mark.asyncio
async def test_shared_limiter_blocks_repeated_accepts() -> None:
    tenancy = InMemoryTenancyStore()
    identity = InMemoryIdentityStore()
    service = TenancyService(
        tenancy,
        identity_store=identity,
        rate_limiter=InMemoryRateLimiter(),
        accept_rate_limit=1,
        accept_rate_window_seconds=60,
    )

    owner = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="owner"))
    member = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="member"))
    owner_scope = AccessScope(actor_user_id=owner.id, auth_mode=AuthMode.LOCAL)
    member_scope = AccessScope(actor_user_id=member.id, auth_mode=AuthMode.LOCAL)

    workspace = await service.create_workspace(owner_scope, slug="rate", name="Rate")
    _invitation, token = await service.create_invitation(
        owner_scope, workspace.id, role=Role.EDITOR
    )

    await service.accept_invitation(member_scope, token=token)

    with pytest.raises(RateLimitedError):
        await service.accept_invitation(member_scope, token=token)
