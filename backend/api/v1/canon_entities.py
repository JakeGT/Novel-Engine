"""Canonical project-scoped entity authoring API.

Reads project a stable nested entity representation from the canon graph; writes
build typed operations and commit them through the mutation saga. Every write
requires the client's expected revision (optimistic concurrency).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_canon_entity_service
from backend.domain.canon.entities import CanonEntity
from backend.domain.identity import AccessScope
from backend.services.canon_entity_service import CanonEntityService

router = APIRouter(prefix="/projects/{project_id}/entities", tags=["canon-entities"])


class CanonEntityModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    name: str
    slug: str | None = None
    lifecycle: str
    schema_version_id: str
    created_revision: int
    retired_revision: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CanonEntityListResponse(BaseModel):
    items: list[CanonEntityModel]
    limit: int
    offset: int


class CreateEntityRequest(BaseModel):
    type: str
    name: str
    slug: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int = Field(ge=0)


class UpdateEntityRequest(BaseModel):
    name: str | None = None
    slug: str | None = None
    attributes: dict[str, Any] | None = None
    expected_revision: int = Field(ge=0)


class RetireEntityRequest(BaseModel):
    expected_revision: int = Field(ge=0)


def _model(entity: CanonEntity) -> CanonEntityModel:
    return CanonEntityModel.model_validate(entity)


@router.get("", response_model=CanonEntityListResponse)
async def canon_list_entities(
    project_id: str,
    entity_type: str | None = Query(default=None),
    include_retired: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    scope: AccessScope = Depends(get_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityListResponse:
    entities = await service.list_entities(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_type=entity_type,
        include_retired=include_retired,
        limit=limit,
        offset=offset,
    )
    return CanonEntityListResponse(
        items=[_model(entity) for entity in entities], limit=limit, offset=offset
    )


@router.get("/search", response_model=CanonEntityListResponse)
async def canon_search_entities(
    project_id: str,
    q: str = Query(min_length=1),
    entity_type: str | None = Query(default=None),
    include_retired: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    scope: AccessScope = Depends(get_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityListResponse:
    entities = await service.search(
        scope,
        scope.workspace_id or "",
        project_id,
        q,
        entity_type=entity_type,
        include_retired=include_retired,
        limit=limit,
    )
    return CanonEntityListResponse(
        items=[_model(entity) for entity in entities], limit=limit, offset=0
    )


@router.get("/{entity_id}", response_model=CanonEntityModel)
async def canon_get_entity(
    project_id: str,
    entity_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityModel:
    entity = await service.get(scope, scope.workspace_id or "", project_id, entity_id)
    return _model(entity)


@router.post("", response_model=CanonEntityModel, status_code=201)
async def canon_create_entity(
    project_id: str,
    body: CreateEntityRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityModel:
    result, entity = await service.create(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_type=body.type,
        name=body.name,
        slug=body.slug,
        attributes=body.attributes,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(entity)


@router.patch("/{entity_id}", response_model=CanonEntityModel)
async def canon_update_entity(
    project_id: str,
    entity_id: str,
    body: UpdateEntityRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityModel:
    result, entity = await service.edit(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_id,
        name=body.name,
        slug=body.slug,
        attributes=body.attributes,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(entity)


@router.post("/{entity_id}/retire", response_model=CanonEntityModel)
async def canon_retire_entity(
    project_id: str,
    entity_id: str,
    body: RetireEntityRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityModel:
    result, entity = await service.retire(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_id,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(entity)
