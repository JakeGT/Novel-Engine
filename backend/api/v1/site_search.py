"""The site's search-by-meaning setup: platform administrators only.

The site's embedding service, model and size are set on a page. Saving checks the key
against the service (embedding the single fixed word "lighthouse", never an author's
words), seals it, and never returns it. Changing the provider or the address is as
sensitive as the key, so it needs a recent sign-in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from backend.api.auth import require_platform_admin, require_platform_admin_read
from backend.api.dependencies import get_container, get_settings
from backend.container import Container
from backend.core.config import Settings
from backend.domain.site_search import SignInAgainError
from backend.services.auth_service import AuthenticatedSession
from backend.services.site_search import SiteSearchService, sign_in_needed

router = APIRouter(prefix="/site", tags=["site"])


class SiteSearchSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    dimension: int
    base_url: str | None = None
    api_key: str | None = None


class SearchModelModel(BaseModel):
    id: str
    # The size it gives by default.
    dimension: int
    # It can be asked for a shorter size (Matryoshka).
    adjustable: bool
    # Null when the person must give a size.
    default_dimension: int | None


class SearchProviderModel(BaseModel):
    id: str
    label: str
    group: Literal["Model makers", "Hosting and routing", "Your own"]
    # None for "your own": the person gives the address.
    base_url: str | None
    needs_url: bool
    key_url: str | None
    key_hint: str | None
    # A local "your own" address needs no key.
    key_optional_when_local: bool
    docs: str
    models: list[SearchModelModel]
    # The sizes the service accepts; None when any size works.
    dimension_choices: list[int] | None


class SearchSetupKeyModel(BaseModel):
    set: bool
    # The key's last four characters, never the key.
    hint: str | None
    updated_at: datetime | None


class SearchSetupDetailModel(BaseModel):
    provider: str
    provider_label: str
    model: str
    dimension: int
    # Set only when the address is not the provider's own.
    base_url: str | None
    host: str
    key: SearchSetupKeyModel
    # None when the provider never checked it.
    verified: bool | None
    updated_at: datetime


class BuiltInModel(BaseModel):
    model: str
    dimension: int


class SearchBuildModel(BaseModel):
    done: int
    total: int
    state: Literal["running", "waiting", "failed"]


class SearchIndexModel(BaseModel):
    searchable: bool
    active_model: str | None
    building: bool
    # Present only while a build is in progress.
    build: SearchBuildModel | None
    needs_build: bool


class SiteSearchStateModel(BaseModel):
    # Where the embedder in use comes from: the page, the server's settings, its own
    # model, or off.
    source: Literal["page", "server", "built_in", "off"]
    editable: bool
    kek_configured: bool
    # None when no setup has been saved on the page.
    setup: SearchSetupDetailModel | None
    built_in: BuiltInModel | None
    index: SearchIndexModel
    sign_in_needed: bool


class SaveSearchSetupModel(BaseModel):
    # "unchecked" when the service could not be reached, so the setup was kept unverified.
    check: Literal["ok", "unchecked"]
    message: str
    state: SiteSearchStateModel


def _service(container: Container = Depends(get_container)) -> SiteSearchService:
    return container.site_search_service


async def require_recent_sign_in(
    auth_session: AuthenticatedSession = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedSession:
    if sign_in_needed(settings, auth_session.session):
        raise SignInAgainError("Sign in again to change the site's search setup.")
    return auth_session


@router.get("/search-providers")
async def list_search_providers(
    _admin: AuthenticatedSession = Depends(require_platform_admin_read),
    service: SiteSearchService = Depends(_service),
) -> list[SearchProviderModel]:
    return [SearchProviderModel.model_validate(provider) for provider in service.providers()]


@router.get("/search-setup")
async def get_search_setup(
    auth_session: AuthenticatedSession = Depends(require_platform_admin_read),
    settings: Settings = Depends(get_settings),
    service: SiteSearchService = Depends(_service),
) -> SiteSearchStateModel:
    return SiteSearchStateModel.model_validate(
        await service.state(sign_in_needed=sign_in_needed(settings, auth_session.session))
    )


@router.put("/search-setup")
async def put_search_setup(
    body: SiteSearchSetupRequest,
    auth_session: AuthenticatedSession = Depends(require_recent_sign_in),
    settings: Settings = Depends(get_settings),
    service: SiteSearchService = Depends(_service),
) -> SaveSearchSetupModel:
    return SaveSearchSetupModel.model_validate(
        await service.save(
            body=body,
            actor_user_id=auth_session.user.id,
            sign_in_needed=sign_in_needed(settings, auth_session.session),
        )
    )


@router.delete("/search-setup")
async def delete_search_setup(
    auth_session: AuthenticatedSession = Depends(require_recent_sign_in),
    settings: Settings = Depends(get_settings),
    service: SiteSearchService = Depends(_service),
) -> SiteSearchStateModel:
    return SiteSearchStateModel.model_validate(
        await service.forget(sign_in_needed=sign_in_needed(settings, auth_session.session))
    )


@router.post("/search-setup/build")
async def build_search_setup(
    auth_session: AuthenticatedSession = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
    service: SiteSearchService = Depends(_service),
) -> SiteSearchStateModel:
    return SiteSearchStateModel.model_validate(
        await service.start_build(sign_in_needed=sign_in_needed(settings, auth_session.session))
    )


@router.post("/search-setup/build/cancel")
async def cancel_search_build(
    auth_session: AuthenticatedSession = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
    service: SiteSearchService = Depends(_service),
) -> SiteSearchStateModel:
    return SiteSearchStateModel.model_validate(
        await service.cancel_build(sign_in_needed=sign_in_needed(settings, auth_session.session))
    )
