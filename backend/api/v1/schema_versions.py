"""Project-scoped schema-version governance endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_schema_service
from backend.domain.identity import AccessScope
from backend.services.schema_compiler import migration_plan
from backend.services.schema_service import SchemaService

router = APIRouter(prefix="/projects/{project_id}/schemas", tags=["schema"])


class SchemaVersionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    project_id: str
    version: int
    status: str
    content_hash: str
    compatibility: str
    created_at: datetime
    published_at: datetime | None = None
    # The declarative definition this version was compiled from.
    definition: dict[str, Any] = {}


class SchemaDefinitionRequest(BaseModel):
    definition: dict[str, Any]


class ValidationReportModel(BaseModel):
    valid: bool
    content_hash: str
    compatibility: str
    errors: list[str]
    migration_plan: list[str]
    entity_types: list[str]
    predicates: list[str]


class SchemaDraftResponse(BaseModel):
    version: SchemaVersionModel
    compatibility: str
    migration_plan: list[str]


@router.get("", response_model=list[SchemaVersionModel])
async def list_schema_versions(
    scope: AccessScope = Depends(get_project_scope),
    service: SchemaService = Depends(get_schema_service),
) -> list[SchemaVersionModel]:
    versions = await service.list_versions(scope)
    return [SchemaVersionModel.model_validate(v) for v in versions]


@router.post("/validate", response_model=ValidationReportModel)
async def validate_schema_definition(
    body: SchemaDefinitionRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: SchemaService = Depends(get_schema_service),
) -> ValidationReportModel:
    report = await service.validate_for_project(scope, body.definition)
    return ValidationReportModel(
        valid=report.valid,
        content_hash=report.content_hash,
        compatibility=report.compatibility,
        errors=report.errors,
        migration_plan=report.migration_plan,
        entity_types=report.entity_types,
        predicates=report.predicates,
    )


@router.post("/drafts", response_model=SchemaDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_schema_draft(
    body: SchemaDefinitionRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: SchemaService = Depends(get_schema_service),
) -> SchemaDraftResponse:
    version, report = await service.create_draft(scope, body.definition)
    return SchemaDraftResponse(
        version=SchemaVersionModel.model_validate(version),
        compatibility=report.category.value,
        migration_plan=migration_plan(report),
    )


class EntityTypeModel(BaseModel):
    name: str
    # False for a project extension.
    core: bool
    # JSON Schema for this type's stable attributes; null when attributes are free-form.
    attributes_schema: dict[str, Any] | None = None


class PredicateModel(BaseModel):
    predicate: str
    core: bool
    subject_types: list[str]
    # entity_ref, string, integer, decimal, boolean, enum, quantity, or structured.
    object_shape: str
    # Permitted target entity types when object_shape is entity_ref (empty = any).
    object_types: list[str]
    cardinality: str
    temporal_allowed: bool


class VocabularyModel(BaseModel):
    """The entity types and predicates a write to this project may use right now."""

    schema_version_id: str | None
    schema_version: int | None
    entity_types: list[EntityTypeModel]
    predicates: list[PredicateModel]


@router.get("/vocabulary", response_model=VocabularyModel)
async def schema_vocabulary(
    scope: AccessScope = Depends(get_project_scope),
    service: SchemaService = Depends(get_schema_service),
) -> VocabularyModel:
    """Core vocabulary plus the published project extensions (for schema-derived forms)."""
    vocabulary = await service.vocabulary(scope)
    return VocabularyModel(
        schema_version_id=vocabulary.schema_version_id,
        schema_version=vocabulary.schema_version,
        entity_types=[
            EntityTypeModel(name=name, core=core, attributes_schema=attributes)
            for name, core, attributes in vocabulary.entity_types
        ],
        predicates=[
            PredicateModel(
                predicate=spec.predicate,
                core=core,
                subject_types=sorted(spec.subject_types),
                object_shape=spec.object_shape.value,
                object_types=sorted(spec.object_types),
                cardinality=spec.cardinality.value,
                temporal_allowed=spec.temporal_allowed,
            )
            for spec, core in vocabulary.predicates
        ],
    )


@router.get("/{version_id}", response_model=SchemaVersionModel)
async def get_schema_version(
    version_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: SchemaService = Depends(get_schema_service),
) -> SchemaVersionModel:
    version = await service.get_version(scope, version_id)
    return SchemaVersionModel.model_validate(version)


@router.post("/{version_id}/publish", response_model=SchemaVersionModel)
async def publish_schema_version(
    version_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: SchemaService = Depends(get_schema_service),
) -> SchemaVersionModel:
    version = await service.publish(scope, version_id)
    return SchemaVersionModel.model_validate(version)
