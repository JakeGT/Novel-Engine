"""Provider credential endpoints (metadata only; the secret is never returned)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_workspace_scope, require_workspace_scope
from backend.api.dependencies import get_credential_service
from backend.domain.credentials import Credential, CredentialProvider
from backend.domain.identity import AccessScope
from backend.services.credential_service import CredentialService

router = APIRouter(prefix="/credentials", tags=["credentials"])


class CredentialModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    provider: str
    label: str
    key_fingerprint: str
    key_last4: str
    key_version: int
    status: str
    created_at: datetime
    rotated_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    # Whether the provider accepted the key when it was saved; None when it was not
    # checked (a key from before this change, or one read back from the list).
    verified: bool | None = None

    @classmethod
    def from_domain(cls, credential: Credential) -> CredentialModel:
        return cls(
            id=credential.id,
            workspace_id=credential.workspace_id,
            provider=credential.provider.value,
            label=credential.label,
            key_fingerprint=credential.key_fingerprint,
            key_last4=credential.key_last4,
            key_version=credential.key_version,
            status=credential.status.value,
            created_at=credential.created_at,
            rotated_at=credential.rotated_at,
            last_used_at=credential.last_used_at,
            revoked_at=credential.revoked_at,
            verified=credential.verified,
        )


class CreateCredentialRequest(BaseModel):
    provider: CredentialProvider = CredentialProvider.OPENROUTER
    label: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=8)


class RotateCredentialRequest(BaseModel):
    key: str = Field(min_length=8)


@router.get("")
async def list_credentials(
    scope: AccessScope = Depends(get_workspace_scope),
    service: CredentialService = Depends(get_credential_service),
) -> list[CredentialModel]:
    assert scope.workspace_id is not None
    credentials = await service.list(scope, scope.workspace_id)
    return [CredentialModel.from_domain(credential) for credential in credentials]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_credential(
    body: CreateCredentialRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    service: CredentialService = Depends(get_credential_service),
) -> CredentialModel:
    assert scope.workspace_id is not None
    credential = await service.create(
        scope,
        workspace_id=scope.workspace_id,
        provider=body.provider,
        label=body.label,
        plaintext_key=body.key,
    )
    return CredentialModel.from_domain(credential)


@router.post("/{credential_id}/rotate")
async def rotate_credential(
    credential_id: str,
    body: RotateCredentialRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    service: CredentialService = Depends(get_credential_service),
) -> CredentialModel:
    assert scope.workspace_id is not None
    credential = await service.rotate(scope, scope.workspace_id, credential_id, body.key)
    return CredentialModel.from_domain(credential)


@router.post("/{credential_id}/revoke")
async def revoke_credential(
    credential_id: str,
    scope: AccessScope = Depends(require_workspace_scope),
    service: CredentialService = Depends(get_credential_service),
) -> CredentialModel:
    assert scope.workspace_id is not None
    credential = await service.revoke(scope, scope.workspace_id, credential_id)
    return CredentialModel.from_domain(credential)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: str,
    scope: AccessScope = Depends(require_workspace_scope),
    service: CredentialService = Depends(get_credential_service),
) -> None:
    assert scope.workspace_id is not None
    await service.delete(scope, scope.workspace_id, credential_id)
