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


def _auth(settings, session_store) -> AuthService:
    return AuthService(
        settings=settings,
        identity_provider=FakeIdentityProvider(),
        identity_store=InMemoryIdentityStore(),
        session_store=session_store,
        login_attempt_store=InMemoryLoginAttemptStore(),
    )


@pytest.mark.asyncio
async def test_an_old_session_is_replaced_without_stranding_the_request_that_triggers_it(
    settings_factory,
) -> None:
    from backend.core.security import hash_token
    from backend.domain.identity import utcnow
    from backend.services.auth_service import ROTATION_GRACE_SECONDS

    session_store = InMemorySessionStore()
    auth = _auth(settings_factory(SESSION_ROTATION_SECONDS=0), session_store)
    original = await auth.start_local_login()

    resolved = await auth.resolve(original.id)

    assert resolved is not None and resolved.rotated_session is not None
    assert resolved.session.id != original.id
    # The write token is unchanged, so the request that caused the replacement, which
    # carries the old token, is still accepted. (It used to be refused, and the
    # browser was left signed out an hour after signing in.)
    assert resolved.session.csrf_token == original.csrf_token
    assert AuthService.csrf_valid(resolved.session, original.csrf_token)
    # The old id is recorded only as a hash, and lapses on its own within moments.
    assert resolved.session.rotated_from == hash_token(original.id)
    old = await session_store.get(original.id)
    assert old is not None
    assert (old.expires_at - utcnow()).total_seconds() <= ROTATION_GRACE_SECONDS


@pytest.mark.asyncio
async def test_requests_in_flight_with_the_old_id_still_work_and_do_not_replace_it_again(
    settings_factory,
) -> None:
    session_store = InMemorySessionStore()
    auth = _auth(settings_factory(SESSION_ROTATION_SECONDS=0), session_store)
    original = await auth.start_local_login()
    await auth.resolve(original.id)
    count = len(session_store.sessions)

    # A second request that left the browser before the new cookie arrived.
    again = await auth.resolve(original.id)

    assert again is not None and again.rotated_session is None
    assert again.session.id == original.id
    assert len(session_store.sessions) == count


@pytest.mark.asyncio
async def test_the_old_id_stops_working_once_the_grace_period_is_over(settings_factory) -> None:
    from datetime import timedelta

    from backend.domain.identity import utcnow

    session_store = InMemorySessionStore()
    auth = _auth(settings_factory(SESSION_ROTATION_SECONDS=0), session_store)
    original = await auth.start_local_login()
    resolved = await auth.resolve(original.id)
    assert resolved is not None
    lapsed = session_store.sessions[original.id].model_copy(
        update={"expires_at": utcnow() - timedelta(seconds=1)}
    )
    session_store.sessions[original.id] = lapsed

    assert await auth.resolve(original.id) is None
    # The replacement is unaffected. With rotation at zero it is itself replaced on
    # use, which is the same mechanism continuing, not a failure.
    assert await auth.resolve(resolved.session.id) is not None


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
