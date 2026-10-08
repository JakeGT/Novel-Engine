"""Typed proposition objects and propositions.

A proposition is a subject entity id, a declared predicate, and exactly one typed
object. The object is a closed discriminated union so an entity reference cannot
be smuggled through an untyped string.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


class ObjectKind(StrEnum):
    ENTITY_REF = "entity_ref"
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    ENUM = "enum"
    QUANTITY = "quantity"
    STRUCTURED = "structured"


class _CanonObject(BaseModel):
    model_config = {"extra": "forbid"}


class EntityRefObject(_CanonObject):
    kind: Literal["entity_ref"] = "entity_ref"
    entity_id: str
    project_id: str


class StringObject(_CanonObject):
    kind: Literal["string"] = "string"
    value: str


class IntegerObject(_CanonObject):
    kind: Literal["integer"] = "integer"
    value: int


class DecimalObject(_CanonObject):
    kind: Literal["decimal"] = "decimal"
    value: Decimal


class BooleanObject(_CanonObject):
    kind: Literal["boolean"] = "boolean"
    value: bool


class EnumObject(_CanonObject):
    kind: Literal["enum"] = "enum"
    value: str
    allowed: tuple[str, ...]

    @model_validator(mode="after")
    def _value_in_allowed(self) -> EnumObject:
        if self.value not in self.allowed:
            raise ValueError(f"{self.value!r} is not one of {self.allowed!r}")
        return self


class QuantityObject(_CanonObject):
    kind: Literal["quantity"] = "quantity"
    value: Decimal
    unit: str


class StructuredObject(_CanonObject):
    kind: Literal["structured"] = "structured"
    value: dict[str, Any]


PropositionObject = Annotated[
    EntityRefObject
    | StringObject
    | IntegerObject
    | DecimalObject
    | BooleanObject
    | EnumObject
    | QuantityObject
    | StructuredObject,
    Field(discriminator="kind"),
]


class Proposition(BaseModel):
    """Subject + declared predicate + exactly one typed object."""

    model_config = {"extra": "forbid"}

    subject_id: str
    predicate: str
    object: PropositionObject
