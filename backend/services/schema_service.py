"""Project schema governance service.

Draft → validate → publish. Compilation is deterministic and cached per project.
Publishing is immutable and optimistic; a forbidden transition is rejected and a
non-additive transition is reported with a migration plan (never silently rewriting
canon).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from backend.core.metrics import metrics
from backend.domain.canon.registry import CORE_ENTITY_TYPES, CORE_PREDICATES, PredicateSpec
from backend.domain.errors import ForbiddenError, NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.schema_defs import SchemaDefinition, SchemaDefinitionError
from backend.domain.schemas import (
    SchemaCompatibilityError,
    SchemaGovernanceError,
    SchemaVersion,
)
from backend.domain.tenancy import Permission
from backend.ports import SchemaVersionStorePort
from backend.services.schema_compiler import (
    CompatibilityReport,
    SchemaCompatibility,
    SchemaCompilationError,
    classify_compatibility,
    compile_definition,
    migration_plan,
)


@dataclass
class ValidationReport:
    valid: bool
    content_hash: str
    compatibility: str
    errors: list[str] = field(default_factory=list)
    migration_plan: list[str] = field(default_factory=list)
    entity_types: list[str] = field(default_factory=list)
    predicates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Vocabulary:
    schema_version_id: str | None
    schema_version: int | None
    # (name, is_core, attribute JSON Schema or None)
    entity_types: list[tuple[str, bool, dict[str, Any] | None]]
    # (spec, is_core)
    predicates: list[tuple[PredicateSpec, bool]]


class SchemaService:
    def __init__(self, store: SchemaVersionStorePort, *, tenancy=None, local_mode: bool = False):
        self._store = store
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(self, scope: AccessScope, permission: Permission) -> None:
        if self._local_mode or self._tenancy is None:
            return
        if scope.workspace_id is None or scope.project_id is None:
            raise ForbiddenError("A workspace and project scope are required.")
        await self._tenancy.require_project(scope, scope.workspace_id, scope.project_id, permission)

    async def _audit(self, scope: AccessScope, action: str, target_id: str, detail: dict) -> None:
        if self._tenancy is None or scope.workspace_id is None:
            return
        await self._tenancy.record_workspace_audit(
            scope,
            workspace_id=scope.workspace_id,
            action=action,
            target_type="schema_version",
            target_id=target_id,
            detail=detail,
        )

    def validate(self, definition: SchemaDefinition | dict) -> ValidationReport:
        model = self._parse(definition)
        compiled = compile_definition(model, use_cache=False)
        return ValidationReport(
            valid=True,
            content_hash=compiled.content_hash,
            compatibility=SchemaCompatibility.ADDITIVE.value,
            entity_types=sorted(compiled.entity_types),
            predicates=sorted(compiled.predicates),
        )

    async def validate_for_project(
        self, scope: AccessScope, definition: SchemaDefinition | dict
    ) -> ValidationReport:
        """Validate a definition and report how it compares with the published version.

        An ill-formed definition is reported as ``valid=False`` with its errors, and
        the compatibility category is computed against this project's published
        schema, so the preview matches what a draft or publish would decide.
        """
        await self._authorize(scope, Permission.PROJECT_WRITE)
        workspace_id, project_id = scope.workspace_id or "", scope.project_id or ""
        try:
            model = self._parse(definition)
            compiled = compile_definition(
                model, workspace_id=workspace_id, project_id=project_id, use_cache=False
            )
        except (ValidationError, SchemaGovernanceError) as error:
            metrics.increment(
                "schema_validations_total", "Schema definition validations.", outcome="invalid"
            )
            return ValidationReport(
                valid=False,
                content_hash="",
                compatibility="invalid",
                errors=[getattr(error, "detail", None) or str(error)],
            )
        published = await self._store.get_published(
            workspace_id=workspace_id, project_id=project_id
        )
        previous = published.parsed_definition() if published is not None else None
        report = classify_compatibility(previous, model)
        metrics.increment(
            "schema_validations_total",
            "Schema definition validations.",
            outcome=report.category.value,
        )
        return ValidationReport(
            valid=True,
            content_hash=compiled.content_hash,
            compatibility=report.category.value,
            migration_plan=migration_plan(report),
            entity_types=sorted(compiled.entity_types),
            predicates=sorted(compiled.predicates),
        )

    @staticmethod
    def _parse(definition: SchemaDefinition | dict) -> SchemaDefinition:
        if isinstance(definition, SchemaDefinition):
            return definition
        try:
            return SchemaDefinition.model_validate(definition)
        except (PydanticValidationError, SchemaDefinitionError, SchemaCompilationError) as error:
            raise ValidationError(f"Invalid schema definition: {error}") from error

    async def list_versions(self, scope: AccessScope) -> list[SchemaVersion]:
        await self._authorize(scope, Permission.PROJECT_READ)
        return await self._store.list(
            workspace_id=scope.workspace_id or "", project_id=scope.project_id or ""
        )

    async def vocabulary(self, scope: AccessScope) -> Vocabulary:
        """Entity types and predicates usable in this project: core plus published extensions."""
        await self._authorize(scope, Permission.PROJECT_READ)
        workspace_id, project_id = scope.workspace_id or "", scope.project_id or ""
        published = await self._store.get_published(
            workspace_id=workspace_id, project_id=project_id
        )
        entity_types: list[tuple[str, bool, dict[str, Any] | None]] = [
            (name, True, None) for name in sorted(CORE_ENTITY_TYPES)
        ]
        predicates: list[tuple[PredicateSpec, bool]] = [
            (CORE_PREDICATES[name], True) for name in sorted(CORE_PREDICATES)
        ]
        if published is not None:
            compiled = compile_definition(
                published.definition, workspace_id=workspace_id, project_id=project_id
            )
            for name in sorted(set(compiled.entity_types) - set(CORE_ENTITY_TYPES)):
                model = compiled.entity_models.get(name)
                entity_types.append(
                    (name, False, model.model_json_schema() if model is not None else None)
                )
            for name in sorted(set(compiled.predicate_specs) - set(CORE_PREDICATES)):
                predicates.append((compiled.predicate_specs[name], False))
        return Vocabulary(
            schema_version_id=published.id if published is not None else None,
            schema_version=published.version if published is not None else None,
            entity_types=entity_types,
            predicates=predicates,
        )

    async def get_version(self, scope: AccessScope, version_id: str) -> SchemaVersion:
        await self._authorize(scope, Permission.PROJECT_READ)
        version = await self._store.get(
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
            version_id=version_id,
        )
        if version is None:
            raise NotFoundError(f"Schema version {version_id!r} not found")
        return version

    async def create_draft(
        self, scope: AccessScope, definition: SchemaDefinition | dict
    ) -> tuple[SchemaVersion, CompatibilityReport]:
        await self._authorize(scope, Permission.PROJECT_WRITE)
        model = self._parse(definition)
        compiled = compile_definition(
            model,
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
        )
        published = await self._store.get_published(
            workspace_id=scope.workspace_id or "", project_id=scope.project_id or ""
        )
        previous = published.parsed_definition() if published is not None else None
        report = classify_compatibility(previous, model)
        version = await self._store.create_draft(
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
            definition=compiled.definition,
            content_hash=compiled.content_hash,
            compatibility=report.category.value,
            created_by_user_id=scope.actor_user_id,
        )
        await self._audit(
            scope,
            "schema.drafted",
            version.id,
            {
                "version": version.version,
                "content_hash": version.content_hash,
                "compatibility": report.category.value,
            },
        )
        return version, report

    async def publish(self, scope: AccessScope, version_id: str) -> SchemaVersion:
        await self._authorize(scope, Permission.PROJECT_ADMIN)
        workspace_id = scope.workspace_id or ""
        project_id = scope.project_id or ""
        draft = await self._store.get(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if draft is None:
            raise NotFoundError(f"Schema version {version_id!r} not found")

        published = await self._store.get_published(
            workspace_id=workspace_id, project_id=project_id
        )
        if published is not None and published.id != draft.id:
            report = classify_compatibility(
                published.parsed_definition(), draft.parsed_definition()
            )
            if report.category is SchemaCompatibility.FORBIDDEN:
                metrics.increment(
                    "schema_publishes_total", "Schema publish attempts.", outcome="forbidden"
                )
                raise SchemaCompatibilityError(
                    "This schema change is forbidden: " + "; ".join(migration_plan(report))
                )

        result = await self._store.publish(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if result is None:
            raise SchemaCompatibilityError("Schema version is not a publishable draft.")
        metrics.increment("schema_publishes_total", "Schema publish attempts.", outcome="published")
        await self._audit(
            scope,
            "schema.published",
            result.id,
            {"version": result.version, "content_hash": result.content_hash},
        )
        return result
