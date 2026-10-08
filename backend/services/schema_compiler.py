"""Deterministic schema compiler.

Compiles a validated :class:`SchemaDefinition` into strict runtime Pydantic
validators and a strict JSON Schema suitable for model structured-output requests.
Compilation is pure: identical definitions produce identical output and hashes.
Compiled artifacts are cached per `(workspace, project, content-hash)` so a cache
entry can never be shared across projects.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from backend.domain.schema_defs import (
    Cardinality,
    EntityExtension,
    FieldDef,
    FieldType,
    SchemaDefinition,
    StringFormat,
)

CACHE_MAXSIZE = 256

_FORMAT_PATTERNS: dict[StringFormat, str] = {
    StringFormat.SLUG: r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    StringFormat.EMAIL: r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    StringFormat.URL: r"^https?://\S+$",
    StringFormat.TEXT: r"^[\s\S]*$",
}


class SchemaCompilationError(ValueError):
    """The definition compiled to an unusable validator/schema."""


@dataclass(frozen=True)
class CompiledSchema:
    content_hash: str
    definition: dict[str, Any]
    entity_models: dict[str, type[BaseModel]]
    json_schema: dict[str, Any]
    entity_types: frozenset[str] = field(default_factory=frozenset)
    predicates: frozenset[str] = field(default_factory=frozenset)
    migration_notes: tuple[str, ...] = ()


def compute_content_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_CACHE: OrderedDict[str, CompiledSchema] = OrderedDict()


def _cache_key(workspace_id: str, project_id: str, content_hash: str) -> str:
    return f"{workspace_id}\x1f{project_id}\x1f{content_hash}"


def _cache_get(workspace_id: str, project_id: str, content_hash: str) -> CompiledSchema | None:
    return _CACHE.get(_cache_key(workspace_id, project_id, content_hash))


def _cache_put(workspace_id: str, project_id: str, compiled: CompiledSchema) -> None:
    key = _cache_key(workspace_id, project_id, compiled.content_hash)
    _CACHE[key] = compiled
    _CACHE.move_to_end(key)
    while len(_CACHE) > CACHE_MAXSIZE:
        _CACHE.popitem(last=False)


def clear_cache() -> None:
    _CACHE.clear()


def compile_definition(
    definition: SchemaDefinition | dict[str, Any],
    *,
    workspace_id: str = "",
    project_id: str = "",
    use_cache: bool = True,
) -> CompiledSchema:
    model = (
        definition
        if isinstance(definition, SchemaDefinition)
        else SchemaDefinition.model_validate(definition)
    )
    canonical = model.canonical()
    content_hash = compute_content_hash(canonical)

    if use_cache:
        cached = _cache_get(workspace_id, project_id, content_hash)
        if cached is not None:
            return cached

    entity_models: dict[str, type[BaseModel]] = {}
    entity_json: dict[str, dict[str, Any]] = {}
    for extension in model.entity_extensions:
        entity_models[extension.entity_type] = _compile_entity(extension)
        entity_json[extension.entity_type] = _entity_json_schema(extension)

    compiled = CompiledSchema(
        content_hash=content_hash,
        definition=canonical,
        entity_models=entity_models,
        json_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": model.label,
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "entity_extensions": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": entity_json,
                },
                "predicate_extensions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/predicate"},
                },
            },
            "$defs": {
                "predicate": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "predicate": {"type": "string"},
                        "subject_types": {"type": "array", "items": {"type": "string"}},
                        "object_kind": {"type": "string"},
                        "object_types": {"type": "array", "items": {"type": "string"}},
                        "cardinality": {"enum": [c.value for c in Cardinality]},
                    },
                    "required": ["predicate", "subject_types", "object_kind"],
                }
            },
        },
        entity_types=frozenset(entity_models),
        predicates=frozenset(p.predicate for p in model.predicate_extensions),
    )
    if use_cache:
        _cache_put(workspace_id, project_id, compiled)
    return compiled


def _compile_entity(extension: EntityExtension) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for spec in extension.fields:
        annotation, field_info = _compile_field(spec)
        fields[spec.name] = (annotation, field_info)
    return create_model(
        f"Project{extension.entity_type}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _compile_field(spec: FieldDef) -> tuple[Any, FieldInfoLike]:
    required = spec.required
    default = ... if required else None
    constraints: dict[str, Any] = {}
    if spec.min_length is not None:
        constraints["min_length"] = spec.min_length
    if spec.max_length is not None:
        constraints["max_length"] = spec.max_length
    if spec.minimum is not None:
        constraints["ge"] = spec.minimum
    if spec.maximum is not None:
        constraints["le"] = spec.maximum
    if spec.field_type is FieldType.LIST:
        constraints["max_length"] = spec.max_items

    annotation: Any
    if spec.field_type is FieldType.STRING:
        annotation = str
        if spec.format is not None and spec.format is not StringFormat.TEXT:
            pattern = _FORMAT_PATTERNS[spec.format]
            constraints["pattern"] = pattern
    elif spec.field_type is FieldType.INTEGER:
        annotation = int
    elif spec.field_type is FieldType.DECIMAL:
        annotation = Decimal
    elif spec.field_type is FieldType.BOOLEAN:
        annotation = bool
    elif spec.field_type is FieldType.ENUM:
        annotation = _literal_enum(spec.values)
    elif spec.field_type is FieldType.ENTITY_REF:
        annotation = str
    elif spec.field_type is FieldType.LIST and spec.items is not None:
        item_annotation, _ = _compile_field(spec.items)
        annotation = list[item_annotation]  # type: ignore[valid-type]
    elif spec.field_type is FieldType.RECORD:
        annotation = _compile_record(spec)
    else:  # pragma: no cover - covered by meta-schema validators
        raise SchemaCompilationError(f"unsupported field type {spec.field_type}")

    return annotation, Field(default, **constraints)


def _compile_record(spec: FieldDef) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for nested in spec.fields:
        annotation, field_info = _compile_field(nested)
        fields[nested.name] = (annotation, field_info)
    return create_model(f"Record{spec.name}", __config__=ConfigDict(extra="forbid"), **fields)


def _literal_enum(values: list[str]) -> Any:
    return Literal[tuple(values)]  # type: ignore[valid-type]


def _entity_json_schema(extension: EntityExtension) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for spec in extension.fields:
        properties[spec.name] = _field_json_schema(spec)
        if spec.required:
            required.append(spec.name)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(required),
    }


def _field_json_schema(spec: FieldDef) -> dict[str, Any]:
    if spec.field_type is FieldType.STRING:
        schema: dict[str, Any] = {"type": "string"}
        if spec.format is not None:
            schema["format"] = spec.format.value
        if spec.min_length is not None:
            schema["minLength"] = spec.min_length
        if spec.max_length is not None:
            schema["maxLength"] = spec.max_length
        return schema
    if spec.field_type is FieldType.INTEGER:
        return _numeric_schema("integer", spec)
    if spec.field_type is FieldType.DECIMAL:
        return _numeric_schema("number", spec)
    if spec.field_type is FieldType.BOOLEAN:
        return {"type": "boolean"}
    if spec.field_type is FieldType.ENUM:
        return {"enum": list(spec.values)}
    if spec.field_type is FieldType.ENTITY_REF:
        return {"type": "string", "x-entity-types": list(spec.entity_types)}
    if spec.field_type is FieldType.LIST:
        schema = {"type": "array", "items": _field_json_schema(spec.items)}  # type: ignore[arg-type]
        if spec.max_items is not None:
            schema["maxItems"] = spec.max_items
        return schema
    if spec.field_type is FieldType.RECORD:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {n.name: _field_json_schema(n) for n in spec.fields},
            "required": sorted(n.name for n in spec.fields if n.required),
        }
    raise SchemaCompilationError(f"unsupported field type {spec.field_type}")  # pragma: no cover


def _numeric_schema(kind: str, spec: FieldDef) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": kind}
    if spec.minimum is not None:
        schema["minimum"] = spec.minimum
    if spec.maximum is not None:
        schema["maximum"] = spec.maximum
    return schema


# Local alias so the return annotation reads clearly without importing Field's alias.
FieldInfoLike = Any


class SchemaCompatibility(StrEnum):
    ADDITIVE = "additive"
    MIGRATION_REQUIRED = "migration_required"
    FORBIDDEN = "forbidden"


_ORDER = {
    SchemaCompatibility.ADDITIVE: 0,
    SchemaCompatibility.MIGRATION_REQUIRED: 1,
    SchemaCompatibility.FORBIDDEN: 2,
}


@dataclass(frozen=True)
class CompatibilityReport:
    category: SchemaCompatibility
    notes: tuple[str, ...]


def classify_compatibility(
    previous: SchemaDefinition | None, current: SchemaDefinition
) -> CompatibilityReport:
    """Classify the transition from `previous` to `current`."""
    if previous is None:
        return CompatibilityReport(SchemaCompatibility.ADDITIVE, ("initial schema",))

    category = SchemaCompatibility.ADDITIVE
    notes: list[str] = []

    def bump(new: SchemaCompatibility, note: str) -> None:
        nonlocal category
        if _ORDER[new] > _ORDER[category]:
            category = new
        notes.append(f"{new.value}: {note}")

    previous_types = {e.entity_type: e for e in previous.entity_extensions}
    current_types = {e.entity_type: e for e in current.entity_extensions}

    for name, old in previous_types.items():
        new = current_types.get(name)
        if new is None:
            bump(SchemaCompatibility.FORBIDDEN, f"entity type {name!r} removed")
            continue
        _compare_fields(name, old.fields, new.fields, bump)
    for name in current_types.keys() - previous_types.keys():
        bump(SchemaCompatibility.ADDITIVE, f"entity type {name!r} added")

    previous_predicates = {p.predicate: p for p in previous.predicate_extensions}
    current_predicates = {p.predicate: p for p in current.predicate_extensions}
    for name, old_pred in previous_predicates.items():
        new_pred = current_predicates.get(name)
        if new_pred is None:
            bump(SchemaCompatibility.FORBIDDEN, f"predicate {name!r} removed")
        elif (
            set(old_pred.subject_types) != set(new_pred.subject_types)
            or old_pred.object_kind != new_pred.object_kind
            or set(old_pred.object_types) != set(new_pred.object_types)
        ):
            bump(SchemaCompatibility.FORBIDDEN, f"predicate {name!r} signature changed")
        elif old_pred.cardinality != new_pred.cardinality:
            bump(SchemaCompatibility.MIGRATION_REQUIRED, f"predicate {name!r} cardinality changed")
    for name in current_predicates.keys() - previous_predicates.keys():
        bump(SchemaCompatibility.ADDITIVE, f"predicate {name!r} added")

    return CompatibilityReport(category, tuple(notes))


def _compare_fields(
    name: str, old_fields: list[FieldDef], new_fields: list[FieldDef], bump
) -> None:
    old_by_name = {f.name: f for f in old_fields}
    new_by_name = {f.name: f for f in new_fields}
    for fname, old in old_by_name.items():
        new = new_by_name.get(fname)
        if new is None:
            bump(SchemaCompatibility.FORBIDDEN, f"{name}.{fname} removed")
            continue
        if old.field_type != new.field_type:
            bump(SchemaCompatibility.FORBIDDEN, f"{name}.{fname} type changed")
            continue
        if set(old.values) - set(new.values):
            bump(SchemaCompatibility.FORBIDDEN, f"{name}.{fname} enum value removed")
        elif set(new.values) - set(old.values):
            bump(SchemaCompatibility.ADDITIVE, f"{name}.{fname} enum value added")
        if set(old.entity_types) - set(new.entity_types):
            bump(SchemaCompatibility.FORBIDDEN, f"{name}.{fname} entity type removed")
        if not old.required and new.required:
            bump(SchemaCompatibility.MIGRATION_REQUIRED, f"{name}.{fname} became required")
        elif old.required and not new.required:
            bump(SchemaCompatibility.ADDITIVE, f"{name}.{fname} became optional")
        if _tightened(old, new):
            bump(SchemaCompatibility.MIGRATION_REQUIRED, f"{name}.{fname} constraint tightened")
    for fname in new_by_name.keys() - old_by_name.keys():
        added = new_by_name[fname]
        if added.required:
            bump(SchemaCompatibility.MIGRATION_REQUIRED, f"{name}.{fname} required field added")
        else:
            bump(SchemaCompatibility.ADDITIVE, f"{name}.{fname} optional field added")


def _tightened(old: FieldDef, new: FieldDef) -> bool:
    def more(a, b, *, higher_is_tighter: bool) -> bool:
        if a is None or b is None:
            return b is not None and a is None  # adding a bound tightens
        return b > a if higher_is_tighter else b < a

    return (
        more(old.min_length, new.min_length, higher_is_tighter=True)
        or more(old.max_length, new.max_length, higher_is_tighter=False)
        or more(old.minimum, new.minimum, higher_is_tighter=True)
        or more(old.maximum, new.maximum, higher_is_tighter=False)
        or more(old.max_items, new.max_items, higher_is_tighter=False)
    )


def migration_plan(report: CompatibilityReport) -> list[str]:
    return [note for note in report.notes if not note.startswith("additive:")]
