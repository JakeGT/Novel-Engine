import pytest

from backend.domain.canon.propositions import ObjectKind
from backend.domain.schema_defs import (
    Cardinality,
    EntityExtension,
    FieldDef,
    FieldType,
    PredicateExtension,
    SchemaDefinition,
)
from backend.services.schema_compiler import SchemaCompatibility, classify_compatibility

pytestmark = pytest.mark.unit


def _def(
    *,
    fields: list[FieldDef] | None = None,
    predicates: list[PredicateExtension] | None = None,
    types: list[str] | None = None,
) -> SchemaDefinition:
    extensions = [
        EntityExtension(
            entity_type=name,
            fields=fields or [FieldDef(name="note", field_type=FieldType.STRING)],
        )
        for name in (types or ["Ship"])
    ]
    return SchemaDefinition(
        label="x", entity_extensions=extensions, predicate_extensions=predicates or []
    )


def test_initial_publish_is_additive() -> None:
    report = classify_compatibility(None, _def())
    assert report.category is SchemaCompatibility.ADDITIVE


def test_optional_field_added_is_additive() -> None:
    old = _def(fields=[FieldDef(name="note", field_type=FieldType.STRING)])
    new = _def(
        fields=[
            FieldDef(name="note", field_type=FieldType.STRING),
            FieldDef(name="flag", field_type=FieldType.BOOLEAN),
        ]
    )
    assert classify_compatibility(old, new).category is SchemaCompatibility.ADDITIVE


def test_required_field_added_requires_migration() -> None:
    old = _def(fields=[FieldDef(name="note", field_type=FieldType.STRING)])
    new = _def(
        fields=[
            FieldDef(name="note", field_type=FieldType.STRING),
            FieldDef(name="serial", field_type=FieldType.STRING, required=True),
        ]
    )
    assert classify_compatibility(old, new).category is SchemaCompatibility.MIGRATION_REQUIRED


def test_removed_field_is_forbidden() -> None:
    old = _def(
        fields=[
            FieldDef(name="note", field_type=FieldType.STRING),
            FieldDef(name="gone", field_type=FieldType.STRING),
        ]
    )
    new = _def(fields=[FieldDef(name="note", field_type=FieldType.STRING)])
    assert classify_compatibility(old, new).category is SchemaCompatibility.FORBIDDEN


def test_type_change_is_forbidden() -> None:
    old = _def(fields=[FieldDef(name="size", field_type=FieldType.INTEGER)])
    new = _def(fields=[FieldDef(name="size", field_type=FieldType.STRING)])
    assert classify_compatibility(old, new).category is SchemaCompatibility.FORBIDDEN


def test_enum_value_removed_is_forbidden_added_is_additive() -> None:
    old = _def(fields=[FieldDef(name="kind", field_type=FieldType.ENUM, values=["a", "b"])])
    removed = _def(fields=[FieldDef(name="kind", field_type=FieldType.ENUM, values=["a"])])
    added = _def(fields=[FieldDef(name="kind", field_type=FieldType.ENUM, values=["a", "b", "c"])])
    assert classify_compatibility(old, removed).category is SchemaCompatibility.FORBIDDEN
    assert classify_compatibility(old, added).category is SchemaCompatibility.ADDITIVE


def test_tightened_bound_requires_migration() -> None:
    old = _def(fields=[FieldDef(name="name", field_type=FieldType.STRING, max_length=100)])
    new = _def(fields=[FieldDef(name="name", field_type=FieldType.STRING, max_length=50)])
    assert classify_compatibility(old, new).category is SchemaCompatibility.MIGRATION_REQUIRED


def test_entity_type_removed_is_forbidden() -> None:
    old = _def(types=["Ship", "Dock"])
    new = _def(types=["Ship"])
    assert classify_compatibility(old, new).category is SchemaCompatibility.FORBIDDEN


def test_predicate_changes() -> None:
    base = PredicateExtension(
        predicate="CARRIES",
        subject_types=["Ship"],
        object_kind=ObjectKind.ENTITY_REF,
        object_types=["Cargo"],
    )
    old = _def(predicates=[base])
    removed = _def(predicates=[])
    assert classify_compatibility(old, removed).category is SchemaCompatibility.FORBIDDEN

    many = PredicateExtension(
        predicate="CARRIES",
        subject_types=["Ship"],
        object_kind=ObjectKind.ENTITY_REF,
        object_types=["Cargo"],
        cardinality=Cardinality.ONE,
    )
    old_one = _def(predicates=[many])
    new_many = _def(predicates=[base])
    assert (
        classify_compatibility(old_one, new_many).category is SchemaCompatibility.MIGRATION_REQUIRED
    )
