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
from backend.domain.canon.naming import (
    aliases_problem,
    external_ids_problem,
    name_problem,
    normalize_name,
    slug_problem,
)
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


class RuleEnforcement(StrEnum):
    """How an author story rule is published. Warning is the default (ADR-013)."""

    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class RuleEvaluation:
    """One author-rule result, before it is classified into a finding.

    ``deterministic`` is false when the result depends on a model or heuristic;
    ``complete`` is false when the evaluator lacked inputs it needed (for
    example facts that only extraction could supply). Either makes the result
    advisory no matter how the rule is published.
    """

    rule_id: str
    message: str
    operation_ordinal: int | None = None
    enforcement: RuleEnforcement = RuleEnforcement.WARNING
    deterministic: bool = True
    complete: bool = True


def classify_rule_evaluation(evaluation: RuleEvaluation) -> ValidationSeverity:
    """Author rules block only when explicitly published as blocking *and* certain."""
    if (
        evaluation.enforcement is RuleEnforcement.BLOCKING
        and evaluation.deterministic
        and evaluation.complete
    ):
        return ValidationSeverity.BLOCKING_RULE
    return ValidationSeverity.ADVISORY


class InvariantEvaluator(Protocol):
    """Seam for author story-invariant evaluation (authoring arrives in Phase 2.5).

    Evaluators run after the built-in integrity checks, inside the same commit
    transaction, and can only *add* findings: nothing they return can remove,
    downgrade, or suppress a built-in integrity finding.
    """

    async def evaluate(
        self, operations: Sequence[CanonOperation], *, context: ValidationContext
    ) -> Sequence[RuleEvaluation]: ...


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

    async def find_entity_by_slug(self, slug: str) -> CanonEntity | None:
        """The active entity holding ``slug`` on this branch, if any."""
        ...

    async def find_entity_by_external_id(self, namespace: str, value: str) -> CanonEntity | None:
        """The entity (active or retired) bound to an external identifier, if any."""
        ...

    async def find_entities_by_name(self, entity_type: str, name: str) -> list[CanonEntity]:
        """Active entities of ``entity_type`` whose normalized name equals ``name``."""
        ...

    async def count_active_references(self, entity_id: str) -> int:
        """Open facts/beliefs, live events, and active links that reference the entity."""
        ...


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

    def __init__(self, evaluators: Sequence[InvariantEvaluator] = ()) -> None:
        self._evaluators = tuple(evaluators)

    async def validate(
        self, operations: Sequence[CanonOperation], *, context: ValidationContext
    ) -> DomainValidationReport:
        integrity = await self._validate_integrity(operations, context=context)
        findings = list(integrity.findings)
        for evaluator in self._evaluators:
            try:
                evaluations = await evaluator.evaluate(operations, context=context)
            except Exception as error:
                # A rule that cannot be evaluated is uncertain, so it may inform a
                # reviewer but can never reject a commit.
                findings.append(
                    ValidationFinding(
                        ValidationSeverity.ADVISORY,
                        "rule_evaluation_failed",
                        f"a story rule could not be evaluated ({type(error).__name__})",
                    )
                )
                continue
            for evaluation in evaluations:
                findings.append(
                    ValidationFinding(
                        classify_rule_evaluation(evaluation),
                        f"rule:{evaluation.rule_id}",
                        evaluation.message,
                        evaluation.operation_ordinal,
                    )
                )
        return DomainValidationReport(findings=tuple(findings))

    async def _validate_integrity(
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

        def advisory(index: int, code: str, message: str) -> None:
            findings.append(ValidationFinding(ValidationSeverity.ADVISORY, code, message, index))

        # Handles claimed or released earlier in this batch (None = released).
        batch_slugs: dict[str, str | None] = {}
        batch_external_ids: dict[tuple[str, str], str] = {}
        batch_names: dict[tuple[str, str], str] = {}

        async def validate_handles(
            index: int,
            entity_id: str,
            entity_type: str,
            *,
            name: str,
            slug: str | None,
            aliases: list[str],
            external_ids: dict[str, str],
            check_name: bool,
            check_slug: bool,
            check_external_ids: bool,
        ) -> None:
            problem = name_problem(name)
            if problem is not None:
                blocking(index, "invalid_name", problem)
            problem = aliases_problem(aliases, name=name)
            if problem is not None:
                blocking(index, "invalid_alias", problem)
            if slug is not None and check_slug:
                problem = slug_problem(slug)
                if problem is not None:
                    blocking(index, "invalid_slug", problem)
                else:
                    if slug in batch_slugs:
                        holder_id = batch_slugs[slug]
                    else:
                        holder = await context.reader.find_entity_by_slug(slug)
                        holder_id = holder.id if holder is not None else None
                        # A holder retired earlier in this batch has released the slug.
                        if holder_id is not None:
                            current_holder = await entity(holder_id)
                            if current_holder is None or not current_holder.is_active:
                                holder_id = None
                    if holder_id is not None and holder_id != entity_id:
                        blocking(index, "slug_conflict", f"slug {slug!r} is used by another entity")
                    else:
                        batch_slugs[slug] = entity_id
            if check_external_ids:
                problem = external_ids_problem(external_ids)
                if problem is not None:
                    blocking(index, "invalid_external_id", problem)
                else:
                    for namespace, value in external_ids.items():
                        key = (namespace, value)
                        bound_id = batch_external_ids.get(key)
                        if bound_id is None:
                            bound = await context.reader.find_entity_by_external_id(
                                namespace, value
                            )
                            bound_id = bound.id if bound is not None else None
                        if bound_id is not None and bound_id != entity_id:
                            blocking(
                                index,
                                "external_id_conflict",
                                f"external identifier {namespace}:{value} is bound to "
                                "another entity",
                            )
                        else:
                            batch_external_ids[key] = entity_id
            if check_name and name.strip():
                key = (entity_type, normalize_name(name))
                twin_id = batch_names.get(key)
                if twin_id is None:
                    twins = await context.reader.find_entities_by_name(entity_type, name)
                    twin_id = next((twin.id for twin in twins if twin.id != entity_id), None)
                if twin_id is not None and twin_id != entity_id:
                    advisory(
                        index,
                        "name_collision",
                        f"another active {entity_type} is already named {name!r}",
                    )
                batch_names[key] = entity_id

        async def validate_evidence(index: int, operation: Any) -> None:
            for item in operation.evidence:
                artefact = item.source_artefact_id
                if artefact.startswith("ast_"):
                    asset = await context.reader.get_asset(artefact)
                    if asset is None:
                        blocking(index, "unknown_evidence", f"unknown evidence asset {artefact!r}")
                elif artefact.startswith("asv_"):
                    version = await context.reader.get_asset_version(artefact)
                    if version is None:
                        blocking(
                            index,
                            "unknown_evidence",
                            f"unknown evidence asset version {artefact!r}",
                        )

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
            await validate_evidence(index, operation)

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
                await validate_handles(
                    index,
                    operation.id,
                    operation.type,
                    name=operation.name,
                    slug=operation.slug,
                    aliases=operation.aliases,
                    external_ids=operation.external_ids,
                    check_name=True,
                    check_slug=True,
                    check_external_ids=True,
                )
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
                    aliases=operation.aliases,
                    external_ids=operation.external_ids,
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
                    updated = current.model_copy(
                        update={
                            "name": current.name if operation.name is None else operation.name,
                            "slug": current.slug if operation.slug is None else operation.slug,
                            "attributes": attributes,
                            "aliases": current.aliases
                            if operation.aliases is None
                            else operation.aliases,
                            "external_ids": current.external_ids
                            if operation.external_ids is None
                            else operation.external_ids,
                        }
                    )
                    if (
                        operation.slug is not None
                        and current.slug is not None
                        and operation.slug != current.slug
                    ):
                        batch_slugs[current.slug] = None
                    # Only handles this operation changes are re-checked, so an
                    # unrelated edit never fails on pre-existing data.
                    await validate_handles(
                        index,
                        operation.id,
                        current.type,
                        name=updated.name,
                        slug=updated.slug,
                        aliases=updated.aliases,
                        external_ids=updated.external_ids,
                        check_name=operation.name is not None
                        and normalize_name(operation.name) != normalize_name(current.name),
                        check_slug=operation.slug is not None and operation.slug != current.slug,
                        check_external_ids=operation.external_ids is not None,
                    )
                    entities[operation.id] = updated
            elif isinstance(operation, RetireEntityOp):
                current = await require_active_entity(index, operation.id, "entity")
                if current is not None:
                    references = await context.reader.count_active_references(operation.id)
                    if references:
                        # Retirement preserves history; existing references stay
                        # readable. The reviewer is told what still points here.
                        advisory(
                            index,
                            "referenced_entity",
                            f"{references} active record(s) still reference {operation.id!r}",
                        )
                    if current.slug is not None:
                        batch_slugs[current.slug] = None
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
    "InvariantEvaluator",
    "RuleEnforcement",
    "RuleEvaluation",
    "ValidationContext",
    "ValidationFinding",
    "ValidationSeverity",
    "classify_rule_evaluation",
]
