"""Canonical project-scoped entity authoring API.

Reads project a stable nested entity representation from the canon graph; writes
build typed operations and commit them through the atomic canonical mutation path. Every write
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
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)


class CanonEntityListResponse(BaseModel):
    items: list[CanonEntityModel]
    limit: int
    offset: int


class CreateEntityRequest(BaseModel):
    type: str
    name: str
    # Omit to derive a unique slug from the name.
    slug: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    expected_revision: int = Field(ge=0)


class UpdateEntityRequest(BaseModel):
    name: str | None = None
    slug: str | None = None
    attributes: dict[str, Any] | None = None
    # Omit to keep the current value; a list/mapping replaces it wholesale.
    aliases: list[str] | None = None
    external_ids: dict[str, str] | None = None
    expected_revision: int = Field(ge=0)


class RetireEntityRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    # Retirement is destructive: without this explicit acknowledgement the request
    # is parked as a proposal awaiting review (409 ``review_required``).
    acknowledge_high_impact: bool = False


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


@router.get("/needs-detail", response_model=CanonEntityListResponse)
async def canon_entities_needing_detail(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    scope: AccessScope = Depends(get_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityListResponse:
    """Entries that are only a name so far, as a to-do list for filling in."""
    entities = await service.needs_detail(scope, scope.workspace_id or "", project_id, limit=limit)
    return CanonEntityListResponse(
        items=[_model(entity) for entity in entities], limit=limit, offset=0
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


@router.get("/by-external-id", response_model=CanonEntityModel)
async def canon_get_entity_by_external_id(
    project_id: str,
    namespace: str = Query(min_length=1, max_length=50),
    value: str = Query(min_length=1, max_length=200),
    scope: AccessScope = Depends(get_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityModel:
    entity = await service.get_by_external_id(
        scope, scope.workspace_id or "", project_id, namespace=namespace, value=value
    )
    return _model(entity)


@router.get("/name-collisions", response_model=CanonEntityListResponse)
async def canon_entity_name_collisions(
    project_id: str,
    entity_type: str = Query(min_length=1),
    name: str = Query(min_length=1, max_length=300),
    scope: AccessScope = Depends(get_project_scope),
    service: CanonEntityService = Depends(get_canon_entity_service),
) -> CanonEntityListResponse:
    """Active same-type entities with the same display name.

    Names are not identity, so a collision never blocks a write; clients show
    this as a warning before creating a possible duplicate.
    """
    entities = await service.name_collisions(
        scope, scope.workspace_id or "", project_id, entity_type=entity_type, name=name
    )
    return CanonEntityListResponse(
        items=[_model(entity) for entity in entities], limit=len(entities), offset=0
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
        aliases=body.aliases,
        external_ids=body.external_ids,
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
        aliases=body.aliases,
        external_ids=body.external_ids,
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
        acknowledge_high_impact=body.acknowledge_high_impact,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _model(entity)
