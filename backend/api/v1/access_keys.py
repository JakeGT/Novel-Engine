"""Access keys for other assistants (QRY-003): make, list, withdraw, and see use.

Signed-in, cookie-authenticated routes. The key itself is returned once, in the
answer to the request that made it, and never again. These routes never accept a
key: keys are only for the MCP endpoint.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_workspace_scope, require_workspace_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain.access_keys import DEFAULT_LIFETIME_DAYS, MAX_LIFETIME_DAYS
from backend.domain.identity import AccessScope
from backend.services.access_key_service import AccessKeyService, ListedKey

router = APIRouter(prefix="/access-keys", tags=["access-keys"])


def get_access_keys(container: Container = Depends(get_container)) -> AccessKeyService:
    return container.access_key_service  # type: ignore[no-any-return]


class AccessKeyModel(BaseModel):
    id: str
    name: str
    # The key's last four characters, to tell keys apart. Never the key.
    hint: str
    # Null: every story its holder can read in this workspace.
    project_id: str | None
    may_suggest: bool
    # active, expired or revoked.
    status: str
    # holder, owner, access_lost or account_closed.
    revoked_reason: str | None
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    mine: bool
    # Shown to an owner for someone else's key.
    holder_name: str | None = None


class CreateAccessKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    # Leave out for every story you can read in this workspace.
    project_id: str | None = Field(default=None, max_length=200)
    may_suggest: bool = False
    expires_in_days: int = Field(default=DEFAULT_LIFETIME_DAYS, ge=1, le=MAX_LIFETIME_DAYS)


class CreatedAccessKeyModel(BaseModel):
    access_key: AccessKeyModel
    # Shown only in this answer; it cannot be shown again.
    key: str


class AccessKeyUseModel(BaseModel):
    day: str
    tool: str
    calls: int
    refused: int


def _model(listed: ListedKey) -> AccessKeyModel:
    key = listed.key
    return AccessKeyModel(
        id=key.id,
        name=key.name,
        hint=key.hint,
        project_id=key.project_id,
        may_suggest=key.may_suggest,
        status=key.status().value,
        revoked_reason=key.revoked_reason.value if key.revoked_reason else None,
        created_at=key.created_at,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        mine=listed.mine,
        holder_name=listed.holder_name,
    )


@router.get("")
async def list_access_keys(
    scope: AccessScope = Depends(get_workspace_scope),
    service: AccessKeyService = Depends(get_access_keys),
) -> list[AccessKeyModel]:
    assert scope.workspace_id is not None
    return [_model(item) for item in await service.list_keys(scope, scope.workspace_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_access_key(
    body: CreateAccessKeyRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    service: AccessKeyService = Depends(get_access_keys),
) -> CreatedAccessKeyModel:
    assert scope.workspace_id is not None
    key, raw = await service.create(
        scope,
        scope.workspace_id,
        name=body.name,
        project_id=body.project_id,
        may_suggest=body.may_suggest,
        lifetime_days=body.expires_in_days,
    )
    return CreatedAccessKeyModel(
        access_key=_model(ListedKey(key=key, mine=True, holder_name=None)), key=raw
    )


@router.post("/{key_id}/revoke")
async def revoke_access_key(
    key_id: str,
    scope: AccessScope = Depends(require_workspace_scope),
    service: AccessKeyService = Depends(get_access_keys),
) -> AccessKeyModel:
    assert scope.workspace_id is not None
    key = await service.revoke(scope, scope.workspace_id, key_id)
    return _model(ListedKey(key=key, mine=key.user_id == scope.actor_user_id, holder_name=None))


@router.get("/{key_id}/uses")
async def access_key_uses(
    key_id: str,
    scope: AccessScope = Depends(get_workspace_scope),
    service: AccessKeyService = Depends(get_access_keys),
) -> list[AccessKeyUseModel]:
    assert scope.workspace_id is not None
    uses = await service.uses(scope, scope.workspace_id, key_id)
    return [
        AccessKeyUseModel(day=use.day, tool=use.tool, calls=use.calls, refused=use.refused)
        for use in uses
    ]
