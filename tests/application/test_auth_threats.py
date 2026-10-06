import pytest

from backend.domain.identity import IdentityProviderUnavailable, InvalidIdentityToken
from backend.services.auth_service import AuthService
from tests.doubles import (
    FakeIdentityProvider,
    InMemoryIdentityStore,
    InMemoryLoginAttemptStore,
    InMemorySessionStore,
)

pytestmark = pytest.mark.application

HOSTED = {
    "AUTH_MODE": "hosted",
    "OIDC_ISSUER": "https://idp.test/application/o/novel-engine/",
    "OIDC_CLIENT_ID": "client-id",
    "OIDC_CLIENT_SECRET": "client-secret",
    "OIDC_REDIRECT_URI": "https://app.test/auth/callback",
}


def _auth(settings, provider, session_store):
    return AuthService(
        settings=settings,
        identity_provider=provider,
        identity_store=InMemoryIdentityStore(),
        session_store=session_store,
        login_attempt_store=InMemoryLoginAttemptStore(),
    )


@pytest.mark.asyncio
async def test_identity_provider_outage_blocks_hosted_login(settings_factory) -> None:
    settings = settings_factory(**HOSTED)
    auth = _auth(settings, FakeIdentityProvider(available=False), InMemorySessionStore())

    with pytest.raises(IdentityProviderUnavailable):
        await auth.start_hosted_login()


@pytest.mark.asyncio
async def test_revoked_session_cannot_be_replayed(settings_factory) -> None:
    settings = settings_factory()
    store = InMemorySessionStore()
    auth = _auth(settings, FakeIdentityProvider(), store)

    session = await auth.start_local_login()
    assert await auth.resolve(session.id) is not None

    await auth.logout(session.id)
    assert await auth.resolve(session.id) is None


@pytest.mark.asyncio
async def test_callback_with_unknown_state_is_rejected(settings_factory) -> None:
    settings = settings_factory(**HOSTED)
    auth = _auth(settings, FakeIdentityProvider(), InMemorySessionStore())

    with pytest.raises(InvalidIdentityToken):
        await auth.complete_hosted_login(state="unknown", code="code")
