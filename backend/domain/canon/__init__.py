"""Canon domain kernel.

Dedicated domain models for trustworthy canon: opaque typed identities, typed
propositions, entities, facts, beliefs, events, revisions, and provenance. These
models are provider- and storage-neutral and must not be conflated with the
transport-oriented ``backend.graph.schema.Node`` property bag.
"""

from __future__ import annotations

from backend.domain.canon.assertions import (
    Belief,
    Fact,
    FactStatus,
    TruthAgreement,
    truth_agreement,
)
from backend.domain.canon.entities import (
    CanonEntity,
    EntityLifecycle,
    ensure_active_reference,
    retire_entity,
)
from backend.domain.canon.errors import (
    CanonError,
    CardinalityError,
    CrossProjectReferenceError,
    InvalidPropositionError,
    RetiredReferenceError,
    UnknownPredicateError,
    UnknownTypeError,
)
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.ids import (
    ID_PREFIXES,
    format_canon_id,
    is_canon_id,
)
from backend.domain.canon.propositions import (
    BooleanObject,
    DecimalObject,
    EntityRefObject,
    EnumObject,
    IntegerObject,
    ObjectKind,
    Proposition,
    QuantityObject,
    StringObject,
    StructuredObject,
)
from backend.domain.canon.registry import (
    CORE_ENTITY_TYPES,
    CORE_PREDICATES,
    Cardinality,
    EntityTypeSpec,
    PredicateSpec,
    entity_type_spec,
    predicate_spec,
    validate_entity_reference,
    validate_proposition,
)
from backend.domain.canon.revisions import CanonRevision, OriginKind, Provenance

__all__ = [
    "ID_PREFIXES",
    "format_canon_id",
    "is_canon_id",
    "ObjectKind",
    "EntityRefObject",
    "StringObject",
    "IntegerObject",
    "DecimalObject",
    "BooleanObject",
    "EnumObject",
    "QuantityObject",
    "StructuredObject",
    "Proposition",
    "EntityLifecycle",
    "CanonEntity",
    "retire_entity",
    "ensure_active_reference",
    "FactStatus",
    "Fact",
    "Belief",
    "TruthAgreement",
    "truth_agreement",
    "EventParticipant",
    "Event",
    "OriginKind",
    "Provenance",
    "CanonRevision",
    "Cardinality",
    "EntityTypeSpec",
    "PredicateSpec",
    "CORE_ENTITY_TYPES",
    "CORE_PREDICATES",
    "entity_type_spec",
    "predicate_spec",
    "validate_proposition",
    "validate_entity_reference",
    "CanonError",
    "UnknownTypeError",
    "UnknownPredicateError",
    "InvalidPropositionError",
    "RetiredReferenceError",
    "CrossProjectReferenceError",
    "CardinalityError",
]
