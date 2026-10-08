import pytest
from pydantic import ValidationError

from backend.domain.schema_defs import (
    EntityExtension,
    FieldDef,
    FieldType,
    SchemaDefinition,
)
from backend.services.schema_compiler import clear_cache, compile_definition

pytestmark = pytest.mark.unit


def sample_definition() -> SchemaDefinition:
    return SchemaDefinition(
        label="Harbor extension",
        entity_extensions=[
            EntityExtension(
                entity_type="Ship",
                fields=[
                    FieldDef(
                        name="hull",
                        field_type=FieldType.STRING,
                        required=True,
                        min_length=1,
                        max_length=50,
                    ),
                    FieldDef(name="crew", field_type=FieldType.INTEGER, minimum=0, maximum=1000),
                    FieldDef(
                        name="ship_class", field_type=FieldType.ENUM, values=["sloop", "frigate"]
                    ),
                    FieldDef(
                        name="tags",
                        field_type=FieldType.LIST,
                        items=FieldDef(name="tag", field_type=FieldType.STRING),
                        max_items=5,
                    ),
                    FieldDef(
                        name="captain",
                        field_type=FieldType.ENTITY_REF,
                        entity_types=["Character"],
                    ),
                ],
            )
        ],
    )


def test_compilation_is_deterministic_and_content_addressed() -> None:
    clear_cache()
    first = compile_definition(sample_definition(), workspace_id="ws", project_id="a")
    second = compile_definition(sample_definition(), workspace_id="ws", project_id="a")
    assert first.content_hash == second.content_hash
    assert first is second  # cache hit for the same project


def test_cache_is_isolated_per_project() -> None:
    clear_cache()
    a = compile_definition(sample_definition(), workspace_id="ws", project_id="a")
    b = compile_definition(sample_definition(), workspace_id="ws", project_id="b")
    assert a.content_hash == b.content_hash
    assert a is not b  # never shared across projects


def test_runtime_validator_enforces_types_and_constraints() -> None:
    clear_cache()
    compiled = compile_definition(sample_definition(), workspace_id="ws", project_id="a")
    model = compiled.entity_models["Ship"]
    model.model_validate(
        {"hull": "Sea Sprite", "crew": 12, "ship_class": "sloop", "tags": ["fast"]}
    )
    with pytest.raises(ValidationError):
        model.model_validate({"hull": "ok", "crew": -1})
    with pytest.raises(ValidationError):
        model.model_validate({"hull": "ok", "ship_class": "galleon"})
    with pytest.raises(ValidationError):
        model.model_validate({"hull": "", "crew": 1})
    with pytest.raises(ValidationError):
        model.model_validate({"hull": "ok", "unexpected": True})


def test_json_schema_shape() -> None:
    clear_cache()
    compiled = compile_definition(sample_definition(), workspace_id="ws", project_id="a")
    ship = compiled.json_schema["properties"]["entity_extensions"]["properties"]["Ship"]
    assert ship["additionalProperties"] is False
    assert ship["required"] == ["hull"]
    assert ship["properties"]["ship_class"] == {"enum": ["sloop", "frigate"]}
    assert ship["properties"]["tags"]["type"] == "array"
    assert ship["properties"]["captain"]["x-entity-types"] == ["Character"]


def test_invalid_identifier_rejected() -> None:
    with pytest.raises(ValidationError):
        FieldDef(name="1bad", field_type=FieldType.STRING)


def test_nesting_depth_limit_rejected() -> None:
    deep = FieldDef(name="l3", field_type=FieldType.STRING)
    record3 = FieldDef(name="l2r", field_type=FieldType.RECORD, fields=[deep])
    record2 = FieldDef(name="l1r", field_type=FieldType.RECORD, fields=[record3])
    record1 = FieldDef(name="l0r", field_type=FieldType.RECORD, fields=[record2])
    with pytest.raises(ValidationError):
        SchemaDefinition(
            label="deep",
            entity_extensions=[EntityExtension(entity_type="Thing", fields=[record1])],
        )


def test_list_of_records_and_missing_enum_values_rejected() -> None:
    with pytest.raises(ValidationError):
        FieldDef(
            name="rows",
            field_type=FieldType.LIST,
            items=FieldDef(
                name="row",
                field_type=FieldType.RECORD,
                fields=[FieldDef(name="x", field_type=FieldType.STRING)],
            ),
        )
    with pytest.raises(ValidationError):
        FieldDef(name="state", field_type=FieldType.ENUM)
