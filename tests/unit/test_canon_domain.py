from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.domain.canon import (
    Belief,
    CanonEntity,
    CanonRevision,
    Cardinality,
    CrossProjectReferenceError,
    EntityLifecycle,
    EntityRefObject,
    EnumObject,
    Event,
    EventParticipant,
    Fact,
    InvalidPropositionError,
    OriginKind,
    Proposition,
    Provenance,
    QuantityObject,
    RetiredReferenceError,
    TruthAgreement,
    UnknownPredicateError,
    UnknownTypeError,
    ensure_active_reference,
    format_canon_id,
    is_canon_id,
    predicate_spec,
    retire_entity,
    truth_agreement,
    validate_entity_reference,
    validate_proposition,
)

pytestmark = pytest.mark.unit

NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _entity(**overrides) -> CanonEntity:
    values = {
        "id": "ent_mara",
        "workspace_id": "wsp_1",
        "project_id": "prj_1",
        "branch_id": "brn_root",
        "type": "Character",
        "name": "Mara",
        "schema_version_id": "scv_1",
        "created_revision": 1,
    }
    values.update(overrides)
    return CanonEntity(**values)


def test_opaque_ids_format_and_validate() -> None:
    assert format_canon_id("entity", "mara") == "ent_mara"
    assert is_canon_id("entity", "ent_mara") is True
    assert is_canon_id("fact", "ent_mara") is False
    assert is_canon_id("entity", "mara") is False
    with pytest.raises(KeyError):
        format_canon_id("nope", "x")
    with pytest.raises(ValueError):
        format_canon_id("entity", "bad id!")


def test_proposition_discriminated_union() -> None:
    prop = Proposition.model_validate(
        {
            "subject_id": "ent_mara",
            "predicate": "LOCATED_AT",
            "object": {"kind": "entity_ref", "entity_id": "ent_tower", "project_id": "prj_1"},
        }
    )
    assert isinstance(prop.object, EntityRefObject)

    quantity = Proposition.model_validate(
        {
            "subject_id": "ent_harbor",
            "predicate": "HAS_AREA",
            "object": {"kind": "quantity", "value": "12.5", "unit": "sqkm"},
        }
    )
    assert isinstance(quantity.object, QuantityObject)
    assert str(quantity.object.value) == "12.5"


def test_proposition_rejects_unknown_shape_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Proposition.model_validate(
            {
                "subject_id": "ent_mara",
                "predicate": "LOCATED_AT",
                "object": {"kind": "nonsense", "value": 1},
            }
        )
    with pytest.raises(ValidationError):
        Proposition.model_validate(
            {
                "subject_id": "ent_mara",
                "predicate": "HAS_NAME",
                "object": {"kind": "string", "value": "Mara", "extra": "no"},
            }
        )


def test_enum_object_must_be_in_allowed() -> None:
    with pytest.raises(ValidationError):
        EnumObject(value="evil", allowed=("lawful", "neutral", "chaotic"))
    assert EnumObject(value="lawful", allowed=("lawful",)).value == "lawful"


def test_proposition_equality_is_value_based() -> None:
    a = Proposition(
        subject_id="ent_mara", predicate="HAS_NAME", object={"kind": "string", "value": "Mara"}
    )
    b = Proposition(
        subject_id="ent_mara", predicate="HAS_NAME", object={"kind": "string", "value": "Mara"}
    )
    c = Proposition(
        subject_id="ent_mara", predicate="HAS_NAME", object={"kind": "string", "value": "Ivo"}
    )
    assert a == b
    assert a != c


def test_fact_and_belief_are_distinct_types() -> None:
    proposition = Proposition(
        subject_id="ent_mara", predicate="IS_HAUNTED", object={"kind": "boolean", "value": False}
    )
    fact = Fact(
        id="fct_1",
        workspace_id="wsp_1",
        project_id="prj_1",
        branch_id="brn_root",
        proposition=proposition,
        provenance_id="prv_1",
        created_revision=1,
    )
    belief = Belief(
        id="blf_1",
        workspace_id="wsp_1",
        project_id="prj_1",
        branch_id="brn_root",
        holder_entity_id="ent_mara",
        proposition=proposition,
        acquisition_provenance_id="prv_2",
        created_revision=1,
    )
    assert isinstance(fact, Fact) and not isinstance(belief, Fact)
    assert "is_true" not in Belief.model_fields


def test_truth_agreement_is_derived() -> None:
    proposition = Proposition(
        subject_id="ent_mara", predicate="IS_HAUNTED", object={"kind": "boolean", "value": True}
    )
    belief = Belief(
        id="blf_1",
        workspace_id="wsp_1",
        project_id="prj_1",
        branch_id="brn_root",
        holder_entity_id="ent_mara",
        proposition=proposition,
        acquisition_provenance_id="prv_2",
        created_revision=1,
    )
    agreeing = Fact(
        id="fct_1",
        workspace_id="wsp_1",
        project_id="prj_1",
        branch_id="brn_root",
        proposition=proposition,
        provenance_id="prv_1",
        created_revision=1,
    )
    assert truth_agreement(belief, []) is TruthAgreement.UNKNOWN
    assert truth_agreement(belief, [agreeing]) is TruthAgreement.AGREES

    contradicting = agreeing.model_copy(
        update={
            "proposition": proposition.model_copy(
                update={"object": {"kind": "boolean", "value": False}}
            )
        }
    )
    assert truth_agreement(belief, [contradicting]) is TruthAgreement.DISAGREES


def test_core_registry_validates_shape_and_subject() -> None:
    ok = Proposition(
        subject_id="ent_mara", predicate="HAS_NAME", object={"kind": "string", "value": "Mara"}
    )
    spec = validate_proposition(ok, subject_type="Character")
    assert spec.predicate == "HAS_NAME"

    with pytest.raises(UnknownPredicateError):
        validate_proposition(
            Proposition.model_construct(
                subject_id="ent_mara", predicate="NOPE", object={"kind": "string", "value": "x"}
            ),
            subject_type="Character",
        )
    with pytest.raises(UnknownTypeError):
        validate_proposition(ok, subject_type="Dragon")

    wrong_shape = Proposition(
        subject_id="ent_harbor",
        predicate="HAS_POPULATION",
        object={"kind": "string", "value": "many"},
    )
    with pytest.raises(InvalidPropositionError):
        validate_proposition(wrong_shape, subject_type="Location")

    wrong_subject = Proposition(
        subject_id="ent_mara",
        predicate="MEMBER_OF",
        object={"kind": "entity_ref", "entity_id": "ent_guild", "project_id": "prj_1"},
    )
    with pytest.raises(InvalidPropositionError):
        validate_proposition(wrong_subject, subject_type="Location")


def test_registry_declares_cardinality_and_temporal_flags() -> None:
    assert predicate_spec("OWNS").cardinality is Cardinality.MANY
    assert predicate_spec("MEMBER_OF").cardinality is Cardinality.ONE
    assert predicate_spec("LOCATED_AT").temporal_allowed is True


def test_entity_reference_project_type_and_lifecycle() -> None:
    ref = Proposition(
        subject_id="ent_mara",
        predicate="LOCATED_AT",
        object={"kind": "entity_ref", "entity_id": "ent_tower", "project_id": "prj_1"},
    )
    tower = _entity(id="ent_tower", type="Location")
    validate_entity_reference(ref, subject_project_id="prj_1", target=tower)

    with pytest.raises(CrossProjectReferenceError):
        validate_entity_reference(
            ref,
            subject_project_id="prj_1",
            target=_entity(id="ent_tower", type="Location", project_id="prj_2"),
        )
    with pytest.raises(InvalidPropositionError):
        validate_entity_reference(
            ref, subject_project_id="prj_1", target=_entity(id="ent_tower", type="Faction")
        )
    with pytest.raises(RetiredReferenceError):
        validate_entity_reference(
            ref,
            subject_project_id="prj_1",
            target=retire_entity(_entity(id="ent_tower", type="Location"), revision=5),
        )


def test_retirement_preserves_original_and_blocks_active_reference() -> None:
    entity = _entity()
    retired = retire_entity(entity, revision=7)
    assert retired.lifecycle is EntityLifecycle.RETIRED
    assert retired.retired_revision == 7
    assert entity.lifecycle is EntityLifecycle.ACTIVE
    assert entity.retired_revision is None

    ensure_active_reference(entity, project_id="prj_1")
    with pytest.raises(RetiredReferenceError):
        ensure_active_reference(retired, project_id="prj_1")
    with pytest.raises(CrossProjectReferenceError):
        ensure_active_reference(entity, project_id="prj_other")


def test_event_preserves_fuzzy_world_position_without_fabricating_time() -> None:
    event = Event(
        id="evt_fire",
        workspace_id="wsp_1",
        project_id="prj_1",
        branch_id="brn_root",
        kind="disaster",
        name="Harbor fire",
        participants=[EventParticipant(entity_id="ent_mara", role="witness")],
        location_ids=["ent_harbor"],
        cause_ids=[],
        outcome_ids=[],
        world_position="a few years before the pact",
        provenance_id="prv_1",
        created_revision=3,
    )
    assert event.world_position == "a few years before the pact"
    assert "world_time" not in Event.model_fields


def test_provenance_and_revision_models() -> None:
    provenance = Provenance(
        id="prv_1",
        workspace_id="wsp_1",
        project_id="prj_1",
        branch_id="brn_root",
        origin=OriginKind.MODEL,
        job_id="job_1",
        source_ref="asset:ast_1",
        created_at=NOW,
    )
    assert provenance.origin is OriginKind.MODEL
    revision = CanonRevision(
        revision=4,
        workspace_id="wsp_1",
        project_id="prj_1",
        branch_id="brn_root",
        commit_id="mut_1",
        expected_head=3,
        result_head=4,
        envelope_checksum="abc",
        created_at=NOW,
    )
    assert revision.result_head == 4
