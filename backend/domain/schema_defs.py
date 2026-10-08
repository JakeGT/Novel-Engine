"""Bounded declarative meta-schema for project schema extensions.

Project extensions are data, never code: no Python, templates, regexes, Cypher, or
expressions are evaluated. The safe subset is primitives, enums, constrained
strings/numbers, lists, one level of nested records, and entity references. Limits
on size, depth, enum/list cardinality, identifiers, and descriptions prevent
resource-exhaustion inputs.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.domain.canon.propositions import ObjectKind

IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
DESCRIPTION_MAX = 500
MAX_TYPES = 50
MAX_FIELDS_PER_TYPE = 50
MAX_DEPTH = 3
MAX_ENUM_VALUES = 50
MAX_LIST_ITEMS = 200
MAX_PREDICATES = 100
MAX_NAME_LENGTH = 200


class SchemaDefinitionError(ValueError):
    """A project schema definition violates the bounded meta-schema."""


class FieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    ENUM = "enum"
    LIST = "list"
    RECORD = "record"
    ENTITY_REF = "entity_ref"


class StringFormat(StrEnum):
    """A small vetted set of string formats, mapped to internal patterns.

    Project authors cannot supply arbitrary regular expressions.
    """

    SLUG = "slug"
    EMAIL = "email"
    URL = "url"
    TEXT = "text"


class Cardinality(StrEnum):
    ONE = "one"
    MANY = "many"


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    field_type: FieldType
    required: bool = False
    description: str | None = None
    # string
    format: StringFormat | None = None
    min_length: int | None = None
    max_length: int | None = None
    # numeric
    minimum: float | None = None
    maximum: float | None = None
    # enum
    values: list[str] = Field(default_factory=list)
    # list
    items: FieldDef | None = None
    max_items: int | None = None
    # record
    fields: list[FieldDef] = Field(default_factory=list)
    # entity reference
    entity_types: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not IDENTIFIER_RE.match(value):
            raise SchemaDefinitionError(f"invalid field name {value!r}")
        return value

    @field_validator("description")
    @classmethod
    def _valid_description(cls, value: str | None) -> str | None:
        if value is not None and len(value) > DESCRIPTION_MAX:
            raise SchemaDefinitionError("description exceeds maximum length")
        return value

    @model_validator(mode="after")
    def _check_shape(self) -> FieldDef:
        if self.field_type is FieldType.ENUM:
            if not self.values:
                raise SchemaDefinitionError(f"enum field {self.name!r} requires values")
            if len(self.values) > MAX_ENUM_VALUES:
                raise SchemaDefinitionError(f"enum field {self.name!r} exceeds max values")
        if self.field_type is FieldType.LIST:
            if self.items is None:
                raise SchemaDefinitionError(f"list field {self.name!r} requires items")
            if self.max_items is not None and not (1 <= self.max_items <= MAX_LIST_ITEMS):
                raise SchemaDefinitionError(f"list field {self.name!r} max_items out of bounds")
            if self.items.field_type is FieldType.RECORD:
                raise SchemaDefinitionError("list items may not be records in the safe subset")
        if self.field_type is FieldType.RECORD:
            if not self.fields:
                raise SchemaDefinitionError(f"record field {self.name!r} requires fields")
        if self.field_type is FieldType.ENTITY_REF and not self.entity_types:
            raise SchemaDefinitionError(f"entity_ref field {self.name!r} requires entity_types")
        return self


class EntityExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str
    description: str | None = None
    fields: list[FieldDef] = Field(default_factory=list)

    @field_validator("entity_type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        if not IDENTIFIER_RE.match(value):
            raise SchemaDefinitionError(f"invalid entity type {value!r}")
        return value


class PredicateExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: str
    description: str | None = None
    subject_types: list[str]
    object_kind: ObjectKind
    object_types: list[str] = Field(default_factory=list)
    cardinality: Cardinality = Cardinality.MANY
    temporal_allowed: bool = False

    @field_validator("predicate")
    @classmethod
    def _valid_predicate(cls, value: str) -> str:
        if not IDENTIFIER_RE.match(value):
            raise SchemaDefinitionError(f"invalid predicate {value!r}")
        return value

    @model_validator(mode="after")
    def _check_shape(self) -> PredicateExtension:
        if not self.subject_types:
            raise SchemaDefinitionError(f"predicate {self.predicate!r} requires subject_types")
        if self.object_kind is ObjectKind.ENTITY_REF and not self.object_types:
            raise SchemaDefinitionError(
                f"predicate {self.predicate!r} entity_ref requires object_types"
            )
        return self


class SchemaDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    description: str | None = None
    entity_extensions: list[EntityExtension] = Field(default_factory=list)
    predicate_extensions: list[PredicateExtension] = Field(default_factory=list)
    temporal_flags: dict[str, bool] = Field(default_factory=dict)
    visibility_flags: dict[str, bool] = Field(default_factory=dict)

    @field_validator("label")
    @classmethod
    def _valid_label(cls, value: str) -> str:
        if not (1 <= len(value) <= MAX_NAME_LENGTH):
            raise SchemaDefinitionError("label length out of bounds")
        return value

    @model_validator(mode="after")
    def _check_limits(self) -> SchemaDefinition:
        if len(self.entity_extensions) > MAX_TYPES:
            raise SchemaDefinitionError("too many entity extensions")
        if len(self.predicate_extensions) > MAX_PREDICATES:
            raise SchemaDefinitionError("too many predicate extensions")
        type_names: set[str] = set()
        for extension in self.entity_extensions:
            if extension.entity_type in type_names:
                raise SchemaDefinitionError(f"duplicate entity type {extension.entity_type!r}")
            type_names.add(extension.entity_type)
            if len(extension.fields) > MAX_FIELDS_PER_TYPE:
                raise SchemaDefinitionError(f"too many fields on {extension.entity_type!r}")
            for field in extension.fields:
                _check_depth(field, depth=1)
        predicate_names: set[str] = set()
        for predicate in self.predicate_extensions:
            if predicate.predicate in predicate_names:
                raise SchemaDefinitionError(f"duplicate predicate {predicate.predicate!r}")
            predicate_names.add(predicate.predicate)
        return self

    def canonical(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _check_depth(field: FieldDef, *, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise SchemaDefinitionError("field nesting exceeds maximum depth")
    if field.field_type is FieldType.RECORD:
        for nested in field.fields:
            _check_depth(nested, depth=depth + 1)
    if field.field_type is FieldType.LIST and field.items is not None:
        _check_depth(field.items, depth=depth + 1)


FieldDef.model_rebuild()
