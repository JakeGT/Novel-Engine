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
async def test_hosted_workspace_creation_requires_platform_admin() -> None:
    service = TenancyService(InMemoryTenancyStore(), settings=_hosted_settings())
    scope = AccessScope(actor_user_id="usr_regular", auth_mode=AuthMode.HOSTED)

    with pytest.raises(ForbiddenError):
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
