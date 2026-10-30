import pytest

from backend.core.config import Settings
from backend.domain.errors import ForbiddenError
from backend.domain.identity import AccessScope, AuthMode
from backend.services.tenancy_service import TenancyService
from tests.doubles import InMemoryTenancyStore

pytestmark = pytest.mark.application


def _hosted_settings(**overrides) -> Settings:
    values = {
        "AUTH_MODE": "hosted",
        "OIDC_ISSUER": "https://idp.test/application/o/novel-engine/",
        "OIDC_CLIENT_ID": "client",
        "OIDC_CLIENT_SECRET": "secret",
        "OIDC_REDIRECT_URI": "https://app.test/auth/callback",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_a_signed_in_person_may_create_one_workspace_of_their_own() -> None:
    service = TenancyService(InMemoryTenancyStore(), settings=_hosted_settings())
    scope = AccessScope(actor_user_id="usr_regular", auth_mode=AuthMode.HOSTED)

    first = await service.create_workspace(scope, slug="mine", name="Mine")
    assert first.slug == "mine"
    # One personal workspace, not unlimited self-service.
    with pytest.raises(ForbiddenError, match="already have a workspace"):
        await service.create_workspace(scope, slug="another", name="Another")


@pytest.mark.asyncio
async def test_being_invited_elsewhere_does_not_use_up_the_personal_workspace() -> None:
    from backend.domain.identity import Role

    store = InMemoryTenancyStore()
    service = TenancyService(store, settings=_hosted_settings())
    owner = AccessScope(actor_user_id="usr_owner", auth_mode=AuthMode.HOSTED)
    shared = await service.create_workspace(owner, slug="shared", name="Shared")
    await store.add_membership(workspace_id=shared.id, user_id="usr_guest", role=Role.EDITOR)

    guest = AccessScope(actor_user_id="usr_guest", auth_mode=AuthMode.HOSTED)
    assert (await service.create_workspace(guest, slug="guest", name="Guest")).slug == "guest"


@pytest.mark.asyncio
async def test_personal_workspaces_can_be_turned_off() -> None:
    service = TenancyService(
        InMemoryTenancyStore(), settings=_hosted_settings(ALLOW_PERSONAL_WORKSPACE=False)
    )
    scope = AccessScope(actor_user_id="usr_regular", auth_mode=AuthMode.HOSTED)

    with pytest.raises(ForbiddenError, match="platform administrator"):
        await service.create_workspace(scope, slug="new", name="New")


@pytest.mark.asyncio
async def test_platform_admin_may_create_workspace() -> None:
    settings = _hosted_settings(PLATFORM_ADMIN_USER_IDS="usr_admin")
    service = TenancyService(InMemoryTenancyStore(), settings=settings)
    scope = AccessScope(actor_user_id="usr_admin", auth_mode=AuthMode.HOSTED)

    workspace = await service.create_workspace(scope, slug="new", name="New")
    assert workspace.slug == "new"


@pytest.mark.asyncio
async def test_local_workspace_creation_is_allowed() -> None:
    settings = Settings(_env_file=None, AUTH_MODE="local")
    service = TenancyService(InMemoryTenancyStore(), settings=settings)
    scope = AccessScope(actor_user_id="local-user", auth_mode=AuthMode.LOCAL)

    workspace = await service.create_workspace(scope, slug="local", name="Local")
    assert workspace.slug == "local"
