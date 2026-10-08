"""Versioned core schema registry.

The application owns a fixed core vocabulary of entity types and predicates. A
predicate declares its subject types, the required object shape (and, for entity
references, the permitted target types), cardinality, whether temporal behavior is
reserved, and the default visibility. Project extensions are compiled separately
(2C) and must never weaken these core rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.errors import (
    CrossProjectReferenceError,
    InvalidPropositionError,
    RetiredReferenceError,
    UnknownPredicateError,
    UnknownTypeError,
)
from backend.domain.canon.propositions import EntityRefObject, ObjectKind, Proposition


class Cardinality(StrEnum):
    ONE = "one"
    MANY = "many"


@dataclass(frozen=True)
class PredicateSpec:
    predicate: str
    subject_types: frozenset[str]
    object_shape: ObjectKind
    object_types: frozenset[str] = frozenset()
    cardinality: Cardinality = Cardinality.ONE
    temporal_allowed: bool = False
    visibility_default: str = "project"


@dataclass(frozen=True)
class EntityTypeSpec:
    entity_type: str
    predicates: frozenset[str]


CORE_PREDICATES: dict[str, PredicateSpec] = {
    spec.predicate: spec
    for spec in (
        PredicateSpec(
            "LOCATED_AT",
            frozenset({"Character", "Object", "Faction", "Event"}),
            ObjectKind.ENTITY_REF,
            frozenset({"Location"}),
            temporal_allowed=True,
        ),
        PredicateSpec(
            "OWNS",
            frozenset({"Character", "Faction"}),
            ObjectKind.ENTITY_REF,
            frozenset({"Object"}),
            cardinality=Cardinality.MANY,
        ),
        PredicateSpec(
            "MEMBER_OF",
            frozenset({"Character"}),
            ObjectKind.ENTITY_REF,
            frozenset({"Faction"}),
        ),
        PredicateSpec(
            "PARTICIPATED_IN",
            frozenset({"Character"}),
            ObjectKind.ENTITY_REF,
            frozenset({"Event"}),
            cardinality=Cardinality.MANY,
        ),
        PredicateSpec(
            "KNOWS",
            frozenset({"Character"}),
            ObjectKind.ENTITY_REF,
            frozenset({"Character"}),
            cardinality=Cardinality.MANY,
        ),
        PredicateSpec(
            "RELATED_TO",
            frozenset({"Character"}),
            ObjectKind.ENTITY_REF,
            frozenset({"Character"}),
            cardinality=Cardinality.MANY,
        ),
        PredicateSpec("CAUSED", frozenset({"Event"}), ObjectKind.ENTITY_REF, frozenset({"Event"})),
        PredicateSpec(
            "HAS_NAME", frozenset({"Character", "Location", "Object"}), ObjectKind.STRING
        ),
        PredicateSpec("HAS_COLOR", frozenset({"Object"}), ObjectKind.STRING),
        PredicateSpec("HAS_POPULATION", frozenset({"Location"}), ObjectKind.INTEGER),
        PredicateSpec("HAS_AREA", frozenset({"Location"}), ObjectKind.QUANTITY),
        PredicateSpec("IS_HAUNTED", frozenset({"Location"}), ObjectKind.BOOLEAN),
        PredicateSpec("HAS_ALIGNMENT", frozenset({"Character"}), ObjectKind.ENUM),
        PredicateSpec("HAS_STATUS", frozenset({"Character", "Object", "Event"}), ObjectKind.ENUM),
        PredicateSpec(
            "HAS_DETAIL", frozenset({"Character", "Location", "Object"}), ObjectKind.STRUCTURED
        ),
    )
}

CORE_ENTITY_TYPES: dict[str, EntityTypeSpec] = {
    entity_type: EntityTypeSpec(
        entity_type,
        frozenset(
            name for name, spec in CORE_PREDICATES.items() if entity_type in spec.subject_types
        ),
    )
    for entity_type in (
        "Character",
        "Location",
        "Object",
        "Faction",
        "Event",
        "Concept",
        "System",
        "Resource",
        "Constraint",
        "Law",
        "Pressure",
        "Theme",
        "Goal",
        "Conflict",
        "Secret",
        "WorldState",
        "World",
    )
}


def entity_type_spec(entity_type: str) -> EntityTypeSpec:
    spec = CORE_ENTITY_TYPES.get(entity_type)
    if spec is None:
        raise UnknownTypeError(f"unknown entity type {entity_type!r}")
    return spec


def predicate_spec(predicate: str) -> PredicateSpec:
    spec = CORE_PREDICATES.get(predicate)
    if spec is None:
        raise UnknownPredicateError(f"unknown predicate {predicate!r}")
    return spec


def validate_proposition(proposition: Proposition, *, subject_type: str) -> PredicateSpec:
    """Validate subject type and object shape against the declared predicate."""
    entity_type_spec(subject_type)
    spec = predicate_spec(proposition.predicate)
    if subject_type not in spec.subject_types:
        raise InvalidPropositionError(
            f"predicate {spec.predicate!r} does not accept subject type {subject_type!r}"
        )
    if proposition.object.kind != spec.object_shape.value:
        raise InvalidPropositionError(
            f"predicate {spec.predicate!r} requires a {spec.object_shape.value} object, "
            f"got {proposition.object.kind!r}"
        )
    return spec


def validate_entity_reference(
    proposition: Proposition,
    *,
    subject_project_id: str,
    target: CanonEntity,
) -> None:
    """Validate an entity-reference object's project, target type, and lifecycle."""
    obj = proposition.object
    if not isinstance(obj, EntityRefObject):
        return
    spec = predicate_spec(proposition.predicate)
    if obj.project_id != subject_project_id or target.project_id != subject_project_id:
        raise CrossProjectReferenceError("entity reference crosses a project boundary")
    if spec.object_types and target.type not in spec.object_types:
        raise InvalidPropositionError(
            f"predicate {spec.predicate!r} does not accept target type {target.type!r}"
        )
    if not target.is_active:
        raise RetiredReferenceError(f"entity {target.id!r} is retired")
