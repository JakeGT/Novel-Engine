import pytest

from backend.domain.identity import AccessScope, AuthMode, IdentityClaims, Role
from backend.services.auth_service import AuthService
from backend.services.tenancy_service import TenancyService
from tests.doubles import (
    FakeIdentityProvider,
    InMemoryIdentityStore,
    InMemoryLoginAttemptStore,
    InMemorySessionStore,
    InMemoryTenancyStore,
)

pytestmark = pytest.mark.application


@pytest.mark.asyncio
async def test_session_rotates_and_revokes_old_id(settings_factory) -> None:
    settings = settings_factory(SESSION_ROTATION_SECONDS=0)
    identity_store = InMemoryIdentityStore()
    session_store = InMemorySessionStore()
    auth = AuthService(
        settings=settings,
        identity_provider=FakeIdentityProvider(),
        identity_store=identity_store,
        session_store=session_store,
        login_attempt_store=InMemoryLoginAttemptStore(),
    )

    original = await auth.start_local_login()
    resolved = await auth.resolve(original.id)

    assert resolved is not None
    assert resolved.rotated_session is not None
    assert resolved.session.id != original.id
    assert resolved.session.rotated_from == original.id
    assert await session_store.get(original.id) is None
    assert (await session_store.get(resolved.session.id)) is not None


@pytest.mark.asyncio
async def test_role_change_revokes_member_sessions() -> None:
    tenancy = InMemoryTenancyStore()
    identity = InMemoryIdentityStore()
    sessions = InMemorySessionStore()
    service = TenancyService(tenancy, identity_store=identity, session_store=sessions)

    owner = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="owner"))
    member = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="member"))
    owner_scope = AccessScope(actor_user_id=owner.id, auth_mode=AuthMode.LOCAL)
    member_scope = AccessScope(actor_user_id=member.id, auth_mode=AuthMode.LOCAL)

    workspace = await service.create_workspace(owner_scope, slug="rotate", name="Rotate")
    _invitation, token = await service.create_invitation(
        owner_scope, workspace.id, role=Role.EDITOR
    )
    await service.accept_invitation(member_scope, token=token)

    member_session = await sessions.create(user_id=member.id, ttl_seconds=3600, csrf_token="csrf")
    assert await sessions.get(member_session.id) is not None

    await service.set_role(owner_scope, workspace.id, member.id, Role.VIEWER)

    assert await sessions.get(member_session.id) is None
