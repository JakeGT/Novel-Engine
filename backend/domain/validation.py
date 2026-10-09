"""Authoritative canon validation (ADR-012/ADR-013).

All canon validation that can block a commit runs inside the canonical
PostgreSQL transaction, against the branch head being advanced. Findings are
split into permanently blocking integrity findings, blocking author-rule
findings (only when a rule is published as blocking and deterministically
evaluable), and advisory findings. There is no suppression path: the canonical
repository invokes this validator on every commit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from backend.domain.canon.assertions import Belief, Fact, FactStatus
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import Event
from backend.domain.canon.propositions import EntityRefObject
from backend.domain.canon.registry import (
    CORE_ENTITY_TYPES,
    CORE_PREDICATES,
    Cardinality,
    PredicateSpec,
)
from backend.domain.media import AssetLink, LinkRole, MediaAsset, MediaAssetVersion, is_link_role
from backend.domain.operations import (
    AmendEventOp,
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    LinkAssetOp,
    RetireAssetLinkOp,
    RetireEntityOp,
    RetireEventOp,
    UpdateEntityOp,
)


class ValidationSeverity(StrEnum):
    INTEGRITY = "integrity"
    BLOCKING_RULE = "blocking_rule"
    ADVISORY = "advisory"


BLOCKING_SEVERITIES = frozenset({ValidationSeverity.INTEGRITY, ValidationSeverity.BLOCKING_RULE})


@dataclass(frozen=True)
class ValidationFinding:
    severity: ValidationSeverity
    code: str
    message: str
    operation_ordinal: int | None = None


@dataclass(frozen=True)
class DomainValidationReport:
    findings: tuple[ValidationFinding, ...] = field(default_factory=tuple)

    @property
    def blocking(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity in BLOCKING_SEVERITIES)

    @property
    def advisory(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity is ValidationSeverity.ADVISORY)

    @property
    def ok(self) -> bool:
        return not self.blocking


class CanonValidationError(RuntimeError):
    """A commit was rejected by the authoritative validator (transaction rolls back)."""

    def __init__(self, report: DomainValidationReport) -> None:
        self.report = report
        detail = "; ".join(f"[{f.code}] {f.message}" for f in report.blocking) or "invalid canon"
        super().__init__(detail)


class CanonTxReader(Protocol):
    """Reads bound to the canonical transaction running the validation."""

    async def get_entity(self, entity_id: str) -> CanonEntity | None: ...

    async def count_open_facts(self, subject_id: str, predicate: str) -> int: ...

    async def get_fact(self, fact_id: str) -> Fact | None: ...

    async def get_belief(self, belief_id: str) -> Belief | None: ...

    async def get_event(self, event_id: str) -> Event | None: ...

    async def get_link(self, link_id: str) -> AssetLink | None: ...

    async def get_asset(self, asset_id: str) -> MediaAsset | None: ...

    async def get_asset_version(self, version_id: str) -> MediaAssetVersion | None: ...


@dataclass(frozen=True)
class ValidationContext:
    workspace_id: str
    project_id: str
    branch_id: str
    schema_version_id: str | None
    head_revision: int
    reader: CanonTxReader
    allowed_entity_types: frozenset[str] = field(
        default_factory=lambda: frozenset(CORE_ENTITY_TYPES)
    )
    entity_attribute_models: dict[str, type[BaseModel]] = field(default_factory=dict)
    predicate_specs: dict[str, PredicateSpec] = field(default_factory=dict)


class AuthoritativeValidator:
    """Pure, deterministic canon validation over an ordered operation set.

    Depends only on the domain registry and a transaction-bound reader, so it can
    run inside the canonical PostgreSQL transaction without service coupling.
    """

    async def validate(
        self, operations: Sequence[CanonOperation], *, context: ValidationContext
    ) -> DomainValidationReport:
        findings: list[ValidationFinding] = []
        entities: dict[str, CanonEntity | None] = {}
        facts: dict[str, Fact | None] = {}
        beliefs: dict[str, Belief | None] = {}
        events: dict[str, Event | None] = {}
        links: dict[str, AssetLink | None] = {}
        open_cardinality: dict[tuple[str, str], int] = {}
        created_record_ids: set[str] = set()

        async def entity(entity_id: str) -> CanonEntity | None:
            if entity_id not in entities:
                entities[entity_id] = await context.reader.get_entity(entity_id)
            return entities[entity_id]

        async def fact(fact_id: str) -> Fact | None:
            if fact_id not in facts:
                facts[fact_id] = await context.reader.get_fact(fact_id)
            return facts[fact_id]

        async def belief(belief_id: str) -> Belief | None:
            if belief_id not in beliefs:
                beliefs[belief_id] = await context.reader.get_belief(belief_id)
            return beliefs[belief_id]

        async def event(event_id: str) -> Event | None:
            if event_id not in events:
                events[event_id] = await context.reader.get_event(event_id)
            return events[event_id]

        async def link(link_id: str) -> AssetLink | None:
            if link_id not in links:
                links[link_id] = await context.reader.get_link(link_id)
            return links[link_id]

        def blocking(index: int, code: str, message: str) -> None:
            findings.append(ValidationFinding(ValidationSeverity.INTEGRITY, code, message, index))

        async def require_active_entity(
            index: int, entity_id: str, role: str
        ) -> CanonEntity | None:
            target = await entity(entity_id)
            if target is None:
                blocking(index, "unknown_reference", f"unknown {role} {entity_id!r}")
                return None
            if target.lifecycle is not EntityLifecycle.ACTIVE:
                blocking(index, "retired_reference", f"{role} {entity_id!r} is retired")
                return None
            return target

        async def validate_proposition(index: int, proposition: Any) -> None:
            subject = await require_active_entity(index, proposition.subject_id, "subject")
            if subject is None:
                return
            spec = context.predicate_specs.get(proposition.predicate) or CORE_PREDICATES.get(
                proposition.predicate
            )
            if spec is None:
                blocking(index, "unknown_predicate", f"unknown predicate {proposition.predicate!r}")
                return
            if subject.type not in spec.subject_types:
                blocking(
                    index,
                    "invalid_proposition",
                    f"predicate {spec.predicate!r} does not accept subject type {subject.type!r}",
                )
            if proposition.object.kind != spec.object_shape.value:
                blocking(
                    index,
                    "invalid_proposition",
                    f"predicate {spec.predicate!r} requires a {spec.object_shape.value} object, "
                    f"got {proposition.object.kind!r}",
                )
                return
            if isinstance(proposition.object, EntityRefObject):
                if proposition.object.project_id != context.project_id:
                    blocking(index, "cross_project_reference", "entity reference crosses project")
                    return
                target = await require_active_entity(
                    index, proposition.object.entity_id, "object reference"
                )
                if (
                    target is not None
                    and spec.object_types
                    and target.type not in spec.object_types
                ):
                    blocking(
                        index,
                        "invalid_proposition",
                        f"predicate {spec.predicate!r} does not accept target type {target.type!r}",
                    )

        for index, operation in enumerate(operations):
            if not 0.0 <= getattr(operation, "confidence", 1.0) <= 1.0:
                blocking(index, "invalid_confidence", "confidence must be between 0 and 1")

            if isinstance(operation, CreateEntityOp):
                if operation.id in created_record_ids or await entity(operation.id) is not None:
                    blocking(index, "duplicate_id", f"record id {operation.id!r} already exists")
                    continue
                created_record_ids.add(operation.id)
                if operation.type not in context.allowed_entity_types:
                    blocking(index, "unknown_type", f"unknown entity type {operation.type!r}")
                    continue
                model = context.entity_attribute_models.get(operation.type)
                if model is not None:
                    try:
                        model.model_validate(operation.attributes)
                    except PydanticValidationError as error:
                        blocking(index, "invalid_attributes", str(error))
                entities[operation.id] = CanonEntity(
                    id=operation.id,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    branch_id=context.branch_id,
                    type=operation.type,
                    name=operation.name,
                    slug=operation.slug,
                    schema_version_id=context.schema_version_id or "",
                    created_revision=context.head_revision + 1,
                    attributes=operation.attributes,
                )
            elif isinstance(operation, UpdateEntityOp):
                current = await require_active_entity(index, operation.id, "entity")
                if current is not None:
                    attributes = (
                        current.attributes if operation.attributes is None else operation.attributes
                    )
                    model = context.entity_attribute_models.get(current.type)
                    if model is not None:
                        try:
                            model.model_validate(attributes)
                        except PydanticValidationError as error:
                            blocking(index, "invalid_attributes", str(error))
                    entities[operation.id] = current.model_copy(
                        update={
                            "name": current.name if operation.name is None else operation.name,
                            "slug": current.slug if operation.slug is None else operation.slug,
                            "attributes": attributes,
                        }
                    )
            elif isinstance(operation, RetireEntityOp):
                current = await require_active_entity(index, operation.id, "entity")
                if current is not None:
                    entities[operation.id] = current.model_copy(
                        update={"lifecycle": EntityLifecycle.RETIRED}
                    )
            elif isinstance(operation, AssertFactOp):
                if operation.id in created_record_ids or await fact(operation.id) is not None:
                    blocking(index, "duplicate_id", f"record id {operation.id!r} already exists")
                    continue
                created_record_ids.add(operation.id)
                await validate_proposition(index, operation.proposition)
                if (
                    operation.valid_from is not None
                    and operation.valid_until is not None
                    and operation.valid_from >= operation.valid_until
                ):
                    blocking(index, "invalid_interval", "valid_from must be before valid_until")
                spec = context.predicate_specs.get(
                    operation.proposition.predicate
                ) or CORE_PREDICATES.get(operation.proposition.predicate)
                if (
                    spec is not None
                    and not spec.temporal_allowed
                    and (operation.valid_from is not None or operation.valid_until is not None)
                ):
                    blocking(
                        index,
                        "invalid_temporal_fact",
                        f"predicate {spec.predicate!r} does not allow a world-time interval",
                    )
                key = (operation.proposition.subject_id, operation.proposition.predicate)
                if spec is not None and spec.cardinality is Cardinality.ONE:
                    if key not in open_cardinality:
                        open_cardinality[key] = await context.reader.count_open_facts(*key)
                    if open_cardinality[key] > 0:
                        blocking(
                            index,
                            "cardinality_conflict",
                            f"predicate {operation.proposition.predicate!r} already asserted for "
                            f"{operation.proposition.subject_id!r}",
                        )
                    open_cardinality[key] += 1
                facts[operation.id] = Fact(
                    id=operation.id,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    branch_id=context.branch_id,
                    proposition=operation.proposition,
                    confidence=operation.confidence,
                    provenance_id="pending",
                    created_revision=context.head_revision + 1,
                    valid_from=operation.valid_from,
                    valid_until=operation.valid_until,
                )
            elif isinstance(operation, CloseFactOp):
                fact_current = await fact(operation.id)
                if fact_current is None:
                    blocking(index, "unknown_fact", f"fact {operation.id!r} does not exist")
                elif (
                    fact_current.status is FactStatus.CLOSED
                    or fact_current.closed_revision is not None
                ):
                    blocking(index, "closed_fact", f"fact {operation.id!r} is already closed")
                else:
                    spec = context.predicate_specs.get(
                        fact_current.proposition.predicate
                    ) or CORE_PREDICATES.get(fact_current.proposition.predicate)
                    key = (
                        fact_current.proposition.subject_id,
                        fact_current.proposition.predicate,
                    )
                    if spec is not None and spec.cardinality is Cardinality.ONE:
                        if key not in open_cardinality:
                            open_cardinality[key] = await context.reader.count_open_facts(*key)
                        open_cardinality[key] = max(0, open_cardinality[key] - 1)
                    facts[operation.id] = fact_current.model_copy(
                        update={
                            "status": FactStatus.CLOSED,
                            "closed_revision": context.head_revision + 1,
                        }
                    )
            elif isinstance(operation, AssertBeliefOp):
                if operation.id in created_record_ids or await belief(operation.id) is not None:
                    blocking(index, "duplicate_id", f"record id {operation.id!r} already exists")
                    continue
                created_record_ids.add(operation.id)
                holder = await require_active_entity(index, operation.holder_entity_id, "holder")
                if holder is not None and holder.type != "Character":
                    blocking(index, "invalid_holder", "belief holder must be a Character")
                if not operation.source_ref:
                    blocking(index, "missing_source", "belief requires an acquisition source_ref")
                await validate_proposition(index, operation.proposition)
                beliefs[operation.id] = Belief(
                    id=operation.id,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    branch_id=context.branch_id,
                    holder_entity_id=operation.holder_entity_id,
                    proposition=operation.proposition,
                    confidence=operation.confidence,
                    acquisition_provenance_id="pending",
                    source_ref=operation.source_ref,
                    created_revision=context.head_revision + 1,
                )
            elif isinstance(operation, CloseBeliefOp):
                belief_current = await belief(operation.id)
                if belief_current is None:
                    blocking(index, "unknown_belief", f"belief {operation.id!r} does not exist")
                elif belief_current.closed_revision is not None:
                    blocking(index, "closed_belief", f"belief {operation.id!r} is already closed")
                else:
                    beliefs[operation.id] = belief_current.model_copy(
                        update={"closed_revision": context.head_revision + 1}
                    )
            elif isinstance(operation, CreateEventOp):
                if operation.id in created_record_ids or await event(operation.id) is not None:
                    blocking(index, "duplicate_id", f"record id {operation.id!r} already exists")
                    continue
                created_record_ids.add(operation.id)
                for participant in operation.participants:
                    await require_active_entity(index, participant.entity_id, "event participant")
                    if not participant.role.strip():
                        blocking(index, "missing_role", "event participant role is required")
                for location_id in operation.location_ids:
                    location = await require_active_entity(index, location_id, "event location")
                    if location is not None and location.type != "Location":
                        blocking(index, "invalid_location", "event location must be a Location")
                for ref in (*operation.cause_ids, *operation.outcome_ids):
                    referenced_entity = await entity(ref)
                    referenced_event = await event(ref)
                    if referenced_entity is None and referenced_event is None:
                        blocking(index, "unknown_reference", f"unknown event reference {ref!r}")
                    elif referenced_entity is not None and not referenced_entity.is_active:
                        blocking(index, "retired_reference", f"event reference {ref!r} is retired")
                    elif (
                        referenced_event is not None
                        and referenced_event.retired_revision is not None
                    ):
                        blocking(index, "retired_reference", f"event reference {ref!r} is retired")
                events[operation.id] = Event(
                    id=operation.id,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    branch_id=context.branch_id,
                    kind=operation.kind,
                    name=operation.name,
                    participants=operation.participants,
                    location_ids=operation.location_ids,
                    cause_ids=operation.cause_ids,
                    outcome_ids=operation.outcome_ids,
                    world_position=operation.world_position,
                    provenance_id="pending",
                    created_revision=context.head_revision + 1,
                )
            elif isinstance(operation, AmendEventOp):
                event_current = await event(operation.id)
                if event_current is None:
                    blocking(index, "unknown_event", f"event {operation.id!r} does not exist")
                elif event_current.retired_revision is not None:
                    blocking(index, "retired_event", f"event {operation.id!r} is retired")
                else:
                    events[operation.id] = event_current.model_copy(
                        update={
                            "name": event_current.name
                            if operation.name is None
                            else operation.name,
                            "world_position": event_current.world_position
                            if operation.world_position is None
                            else operation.world_position,
                        }
                    )
            elif isinstance(operation, RetireEventOp):
                event_current = await event(operation.id)
                if event_current is None:
                    blocking(index, "unknown_event", f"event {operation.id!r} does not exist")
                elif event_current.retired_revision is not None:
                    blocking(index, "retired_event", f"event {operation.id!r} is already retired")
                else:
                    events[operation.id] = event_current.model_copy(
                        update={"retired_revision": context.head_revision + 1}
                    )
            elif isinstance(operation, LinkAssetOp):
                if operation.id in created_record_ids or await link(operation.id) is not None:
                    blocking(index, "duplicate_id", f"record id {operation.id!r} already exists")
                    continue
                created_record_ids.add(operation.id)
                if not is_link_role(operation.role):
                    blocking(index, "invalid_role", f"unknown link role {operation.role!r}")
                asset = await context.reader.get_asset(operation.asset_id)
                if asset is None:
                    blocking(index, "unknown_asset", f"unknown asset {operation.asset_id!r}")
                elif not asset.is_active:
                    blocking(index, "inactive_asset", f"asset {operation.asset_id!r} is not active")
                if operation.asset_version_id is not None:
                    version = await context.reader.get_asset_version(operation.asset_version_id)
                    if version is None:
                        blocking(
                            index,
                            "unknown_asset_version",
                            f"unknown asset version {operation.asset_version_id!r}",
                        )
                    elif version.asset_id != operation.asset_id:
                        blocking(
                            index,
                            "asset_version_mismatch",
                            "asset version belongs to another asset",
                        )
                    elif version.retired_revision is not None:
                        blocking(index, "inactive_asset_version", "asset version is retired")
                if operation.target_record_type != "entity":
                    blocking(
                        index,
                        "unsupported_target",
                        f"unsupported link target type {operation.target_record_type!r}",
                    )
                else:
                    await require_active_entity(index, operation.target_id, "link target")
                links[operation.id] = AssetLink(
                    id=operation.id,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    branch_id=context.branch_id,
                    asset_id=operation.asset_id,
                    asset_version_id=operation.asset_version_id,
                    role=LinkRole(operation.role),
                    target_record_type=operation.target_record_type,
                    target_record_id=operation.target_id,
                    provenance_id="pending",
                    created_revision=context.head_revision + 1,
                )
            elif isinstance(operation, RetireAssetLinkOp):
                link_current = await link(operation.id)
                if link_current is None:
                    blocking(
                        index, "unknown_asset_link", f"asset link {operation.id!r} does not exist"
                    )
                elif not link_current.is_active:
                    blocking(index, "retired_asset_link", f"asset link {operation.id!r} is retired")
                else:
                    links[operation.id] = link_current.model_copy(
                        update={"retired_revision": context.head_revision + 1}
                    )

        return DomainValidationReport(findings=tuple(findings))


__all__ = [
    "AuthoritativeValidator",
    "CanonTxReader",
    "CanonValidationError",
    "DomainValidationReport",
    "ValidationContext",
    "ValidationFinding",
    "ValidationSeverity",
]
