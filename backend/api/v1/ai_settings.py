"""Which AI provider and model a workspace's assistant uses."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_workspace_scope, require_session, require_workspace_scope
from backend.api.dependencies import get_container, get_credential_service, get_usage_service
from backend.container import Container
from backend.domain.identity import AccessScope
from backend.domain.providers import PROVIDERS
from backend.ports import AIProviderError
from backend.services.credential_service import MAX_MODEL_LENGTH, AiChoice, CredentialService
from backend.services.usage_service import MAX_ALLOWANCE, MIN_ALLOWANCE, ProviderUsageService

router = APIRouter(prefix="/ai", tags=["ai"])


class ProviderModel(BaseModel):
    id: str
    label: str
    # Where an owner creates a key for this provider.
    key_url: str
    # A starting suggestion, or null when the owner must choose a model.
    default_model: str | None
    note: str


class AiChoiceModel(BaseModel):
    # Null when no key is connected.
    provider: str | None
    # Null when a key is connected but no model has been chosen for it yet.
    model: str | None
    # True when an owner picked it; false when it follows from the only key connected.
    chosen: bool


class ChooseAiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=MAX_MODEL_LENGTH)


class ModelListModel(BaseModel):
    models: list[str]
    # False when the provider would not list its models; a name can still be typed.
    listed: bool


def _choice(choice: AiChoice) -> AiChoiceModel:
    return AiChoiceModel(provider=choice.provider, model=choice.model, chosen=choice.chosen)


@router.get("/providers", dependencies=[Depends(require_session)])
async def list_providers() -> list[ProviderModel]:
    return [
        ProviderModel(
            id=spec.id,
            label=spec.label,
            key_url=spec.key_url,
            default_model=spec.default_model,
            note=spec.note,
        )
        for spec in PROVIDERS
    ]


class SearchWhereModel(BaseModel):
    """Where an author's writing goes to be searchable by meaning. Set by whoever runs the
    site, not by a workspace's key, so every author is told the same."""

    # server: made on this site's own computer; service: sent to another company's
    # service; off: search by meaning is off, and passages are found by their words.
    kind: Literal["server", "service", "off"]
    # The service's name and host, when it is not this server.
    name: str | None = None
    host: str | None = None
    # Whether the search index for it is built and in use now.
    in_use: bool


def _search_where(container: Container) -> SearchWhereModel:
    embedder: Any = getattr(container, "multimodal_embedder", None)
    if embedder is None or not embedder.available():
        return SearchWhereModel(kind="off", in_use=False)
    searchable = getattr(container.vectors, "searchable", None)
    try:
        in_use = bool(searchable()) if searchable is not None else False
    except Exception:  # noqa: BLE001 - a store that cannot be read is "not in use", said plainly
        in_use = False
    where = getattr(embedder, "where_words_go", None)
    if where is None:
        return SearchWhereModel(kind="server", in_use=in_use)
    found = where()
    if found.kind == "server":
        return SearchWhereModel(kind="server", in_use=in_use)
    return SearchWhereModel(kind="service", name=found.name, host=found.host, in_use=in_use)


@router.get("/search", dependencies=[Depends(require_session)])
async def search_where(container: Container = Depends(get_container)) -> SearchWhereModel:
    # Reading the index's state touches the database without async; keep it off the loop.
    return await asyncio.to_thread(_search_where, container)


@router.get("/settings")
async def get_ai_settings(
    scope: AccessScope = Depends(get_workspace_scope),
    service: CredentialService = Depends(get_credential_service),
) -> AiChoiceModel:
    assert scope.workspace_id is not None
    return _choice(await service.ai_choice(scope, scope.workspace_id))


@router.put("/settings")
async def choose_ai(
    body: ChooseAiRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    service: CredentialService = Depends(get_credential_service),
) -> AiChoiceModel:
    assert scope.workspace_id is not None
    return _choice(
        await service.choose_ai(scope, scope.workspace_id, provider=body.provider, model=body.model)
    )


@router.get("/models")
async def list_models(
    provider: str = Query(min_length=1, max_length=40),
    scope: AccessScope = Depends(get_workspace_scope),
    service: CredentialService = Depends(get_credential_service),
) -> ModelListModel:
    """The models the workspace's key for this provider can use, read from the provider."""
    assert scope.workspace_id is not None
    try:
        return ModelListModel(
            models=await service.list_models(scope, scope.workspace_id, provider), listed=True
        )
    except AIProviderError:
        # Some providers do not offer a list through this route; that is not an error
        # for the author, who can still type a model name.
        return ModelListModel(models=[], listed=False)


class AllowanceModel(BaseModel):
    # How long the allowance covers, in hours.
    period_hours: int
    # The limit in force for one person; null when there is none.
    limit_tokens: int | None
    # What the signed-in person has used in the period.
    used_tokens: int
    requests: int
    # What an owner chose for this workspace, if anything, and the site's own ceiling.
    workspace_limit_tokens: int | None
    site_limit_tokens: int | None
    # What the provider reported the period's calls cost, in US dollars; null when it
    # reported none (OpenRouter does; prices are never worked out here).
    cost_usd: float | None = None


class SetAllowanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Null removes the workspace's own limit, leaving the site's.
    daily_tokens: int | None = Field(default=None, ge=MIN_ALLOWANCE, le=MAX_ALLOWANCE)


async def _allowance(usage: ProviderUsageService, scope: AccessScope) -> AllowanceModel:
    found = await usage.allowance(
        workspace_id=scope.workspace_id or "", actor_user_id=scope.actor_user_id
    )
    return AllowanceModel(
        period_hours=max(1, found.period_seconds // 3600),
        limit_tokens=found.limit_tokens,
        used_tokens=found.used_tokens,
        requests=found.requests,
        workspace_limit_tokens=found.workspace_limit_tokens,
        site_limit_tokens=found.site_limit_tokens,
        cost_usd=found.cost_usd,
    )


@router.get("/usage")
async def get_allowance(
    scope: AccessScope = Depends(get_workspace_scope),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
) -> AllowanceModel:
    """What the signed-in person has spent of their assistant allowance, before they spend more."""
    assert scope.workspace_id is not None
    await credentials.require_use(scope, scope.workspace_id)
    return await _allowance(usage, scope)


@router.put("/usage")
async def set_allowance(
    body: SetAllowanceRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
) -> AllowanceModel:
    """An owner sets how much each person may spend through the workspace's key in a day."""
    assert scope.workspace_id is not None
    await credentials.require_admin(scope, scope.workspace_id)
    await usage.set_allowance(
        workspace_id=scope.workspace_id,
        daily_tokens=body.daily_tokens,
        actor_user_id=scope.actor_user_id,
    )
    return await _allowance(usage, scope)
