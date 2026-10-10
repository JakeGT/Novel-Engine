"""Which AI provider and model a workspace's assistant uses."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_workspace_scope, require_session, require_workspace_scope
from backend.api.dependencies import get_credential_service
from backend.domain.identity import AccessScope
from backend.domain.providers import PROVIDERS
from backend.ports import AIProviderError
from backend.services.credential_service import MAX_MODEL_LENGTH, AiChoice, CredentialService

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
