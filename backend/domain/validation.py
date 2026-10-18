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

from backend.domain.arcs import PATH_KEY, arc_path, path_problem
from backend.domain.canon.assertions import Belief, Fact, FactStatus, FactVisibility
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import Event
from backend.domain.canon.marks import details
from backend.domain.canon.naming import (
    aliases_problem,
    external_ids_problem,
    name_problem,
    normalize_name,
    slug_problem,
)
from backend.domain.canon.plans import (
    ALLOWED_PARENTS,
    MAX_DETAIL_LENGTH,
    MAX_DETAILS,
    MAX_REFS,
    MAX_SUMMARY_LENGTH,
    MAX_THREAD_LINKS,
    MAX_TITLE_LENGTH,
    PLAN_ROLES,
    SINGLE_ROLES,
    PlanItem,
    PlanKind,
    apply_plan_update,
    plan_item_from_create,
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
    AmendFactOp,
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    CreatePlanItemOp,
    LinkAssetOp,
    RetireAssetLinkOp,
    RetireEntityOp,
    RetireEventOp,
    RetirePlanItemOp,
    UpdateEntityOp,
    UpdatePlanItemOp,
)
from backend.domain.worldtime import (
    Direction,
    Relative,
    Resolved,
    Span,
    TimeProblem,
    is_unknown,
    resolve,
    span_of,
    spans_overlap,
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
    # For a story rule's finding, which wording of the rule it came from.
    rule_version: int | None = None


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
    # Which wording of the rule gave this result (CON-015: findings cite rule versions).
    rule_version: int | None = None


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

    async def open_facts(self, subject_id: str, predicate: str) -> list[Fact]:
        """The facts currently asserted for one subject and kind of fact."""
        ...

    async def get_fact(self, fact_id: str) -> Fact | None: ...

    async def get_belief(self, belief_id: str) -> Belief | None: ...

    async def get_event(self, event_id: str) -> Event | None: ...

    async def get_plan_item(self, plan_id: str) -> PlanItem | None: ...

    async def plan_child_ids(self, plan_id: str) -> list[str]:
        """Ids of the active plan items directly under this one."""
        ...

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

    async def active_entities(self, limit: int) -> list[CanonEntity]:
        """The active entities on this branch, at most ``limit`` (for story rules)."""
        ...

    async def asserted_facts(self, limit: int) -> list[Fact]:
        """The facts asserted now on this branch, at most ``limit`` (for story rules)."""
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


def _only_fills(current: PlanItem, change: UpdatePlanItemOp) -> bool:
    """Whether ``change`` only fills in what is empty and adds to what is there."""
    if change.title is not None or change.details is not None:
        return False
    if change.summary is not None and current.summary.strip():
        return False
    if change.refs is not None:
        proposed = {(ref.entity_id, ref.role) for ref in change.refs}
        if not {(ref.entity_id, ref.role) for ref in current.refs} <= proposed:
            return False
        for role in ("point_of_view", "place"):
            held = {ref.entity_id for ref in current.refs if ref.role == role}
            if held and {e for e, r in proposed if r == role} != held:
                return False
    return True


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
                        evaluation.rule_version,
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
        # Open facts per (subject, kind of fact), loaded once and kept current through
        # the batch, so a one-at-a-time kind can be checked span against span.
        open_by_key: dict[tuple[str, str], dict[str, Fact]] = {}
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

        plans: dict[str, PlanItem | None] = {}

        async def plan(plan_id: str) -> PlanItem | None:
            if plan_id not in plans:
                plans[plan_id] = await context.reader.get_plan_item(plan_id)
            return plans[plan_id]

        async def link(link_id: str) -> AssetLink | None:
            if link_id not in links:
                links[link_id] = await context.reader.get_link(link_id)
            return links[link_id]

        def blocking(index: int, code: str, message: str) -> None:
            findings.append(ValidationFinding(ValidationSeverity.INTEGRITY, code, message, index))

        def advisory(index: int, code: str, message: str) -> None:
            findings.append(ValidationFinding(ValidationSeverity.ADVISORY, code, message, index))

        async def resolve_when(when: Any, seen: frozenset[str] = frozenset()) -> Resolved:
            """Resolve a story time, following relative times through events in this batch."""
            if not isinstance(when, Relative):
                return resolve(when, lambda _id: False)
            anchor_id = when.anchor_event_id
            if anchor_id in seen:
                return Resolved(None, TimeProblem.CYCLE)
            anchor = await event(anchor_id)
            if anchor is None:
                return Resolved(None, TimeProblem.MISSING_ANCHOR)
            inner = await resolve_when(anchor.when, seen | {anchor_id})
            if inner.interval is None:
                return inner
            sign = -1 if when.direction is Direction.BEFORE else 1
            return Resolved(inner.interval.shift(sign * when.offset, when.give_or_take))

        async def check_when(index: int, event_id: str, when: Any) -> None:
            resolved = await resolve_when(when, frozenset({event_id}))
            if resolved.problem is TimeProblem.CYCLE:
                blocking(
                    index,
                    "time_cycle",
                    "this time is set relative to events that are set relative to it",
                )
            elif resolved.problem is TimeProblem.MISSING_ANCHOR:
                blocking(index, "unknown_anchor", "this time is set relative to an unknown event")

        async def span_for(record: Fact) -> Span:
            return span_of(await resolve_when(record.starts), await resolve_when(record.ends))

        async def check_span(index: int, record: Fact) -> None:
            for when in (record.starts, record.ends):
                if isinstance(when, Relative):
                    await check_when(index, record.id, when)
            span = await span_for(record)
            if span.start is not None and span.end is not None and span.start.lo >= span.end.hi:
                blocking(index, "invalid_span", "a fact must start before it stops being true")

        async def open_facts(key: tuple[str, str]) -> dict[str, Fact]:
            if key not in open_by_key:
                open_by_key[key] = {item.id: item for item in await context.reader.open_facts(*key)}
            return open_by_key[key]

        async def forget_open(record: Fact) -> None:
            key = (record.proposition.subject_id, record.proposition.predicate)
            (await open_facts(key)).pop(record.id, None)

        async def check_one_at_a_time(index: int, record: Fact) -> None:
            """A one-at-a-time kind of fact may have one value at any moment of story time."""
            key = (record.proposition.subject_id, record.proposition.predicate)
            current = await open_facts(key)
            span = await span_for(record)
            for other in current.values():
                if other.id != record.id and spans_overlap(span, await span_for(other)):
                    blocking(
                        index,
                        "cardinality_conflict",
                        f"predicate {record.proposition.predicate!r} already asserted for "
                        f"{record.proposition.subject_id!r}",
                    )
                    break
            current[record.id] = record

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

        async def validate_path(index: int, item: PlanItem, previous: PlanItem | None) -> None:
            """An arc's stages, however they were written: readable, on an arc, and
            pointing at scenes and facts that are there. Only what the change adds is
            looked up, so a stage left pointing at something since removed never blocks
            an unrelated edit."""
            raw = item.details.get(PATH_KEY)
            if raw is None:
                return
            if item.kind is not PlanKind.ARC:
                blocking(index, "invalid_plan_path", "only an arc can have stages")
                return
            problem = path_problem(raw)
            if problem is not None:
                blocking(index, "invalid_plan_path", problem)
                return
            before = arc_path(previous.details) if previous is not None else []
            old_scenes = {stage.scene_id for stage in before}
            old_facts = {stage.fact_id for stage in before}
            for stage in arc_path(item.details):
                if stage.scene_id is not None and stage.scene_id not in old_scenes:
                    scene = await plan(stage.scene_id)
                    if scene is None or not scene.is_active or scene.kind is not PlanKind.SCENE:
                        blocking(
                            index,
                            "invalid_plan_path",
                            "a stage can only be reached in a scene of the plan",
                        )
                if stage.fact_id is not None and stage.fact_id not in old_facts:
                    tied = await fact(stage.fact_id)
                    if tied is None or tied.closed_revision is not None:
                        blocking(
                            index,
                            "invalid_plan_path",
                            "a stage can only be tied to a fact that is in the story",
                        )

        async def validate_plan_item(
            index: int, item: PlanItem, previous: PlanItem | None = None
        ) -> None:
            """A plan item as it would stand: its text, its place in the outline, what it names."""
            if not item.title.strip() or len(item.title) > MAX_TITLE_LENGTH:
                blocking(
                    index, "invalid_plan_title", "a plan item needs a title of sensible length"
                )
            if len(item.summary) > MAX_SUMMARY_LENGTH:
                blocking(index, "invalid_plan_summary", "the plan item's summary is too long")
            if item.position < 0:
                blocking(index, "invalid_plan_position", "a position cannot be negative")
            if len(item.details) > MAX_DETAILS or any(
                not key.strip() or len(str(value)) > MAX_DETAIL_LENGTH
                for key, value in item.details.items()
            ):
                blocking(index, "invalid_plan_details", "the plan item's details are malformed")
            await validate_path(index, item, previous)

            if item.parent_id is not None:
                allowed = ALLOWED_PARENTS[item.kind]
                seen = {item.id}
                ancestor_id: str | None = item.parent_id
                first = True
                while ancestor_id is not None:
                    if ancestor_id in seen:
                        blocking(index, "plan_cycle", "a plan item cannot sit under itself")
                        break
                    seen.add(ancestor_id)
                    ancestor = await plan(ancestor_id)
                    if ancestor is None or not ancestor.is_active:
                        if first:
                            blocking(
                                index,
                                "unknown_plan_parent",
                                f"plan item {item.parent_id!r} does not exist or is removed",
                            )
                        break
                    if first and ancestor.kind not in allowed:
                        blocking(
                            index,
                            "invalid_plan_parent",
                            f"a {item.kind.value} cannot sit under a {ancestor.kind.value}",
                        )
                    first = False
                    ancestor_id = ancestor.parent_id

            if len(item.refs) > MAX_REFS:
                blocking(index, "invalid_plan_refs", "a plan item names too many entries")
            filled: dict[str, int] = {}
            for ref in item.refs:
                if ref.role not in PLAN_ROLES:
                    blocking(index, "invalid_plan_role", f"unknown plan role {ref.role!r}")
                    continue
                target = await require_active_entity(index, ref.entity_id, f"plan {ref.role}")
                filled[ref.role] = filled.get(ref.role, 0) + 1
                if ref.role in SINGLE_ROLES:
                    wanted = SINGLE_ROLES[ref.role]
                    if filled[ref.role] > 1:
                        blocking(index, "plan_role_taken", f"only one {ref.role} can be named")
                    elif target is not None and wanted is not None and target.type != wanted:
                        blocking(
                            index,
                            "invalid_plan_ref_type",
                            f"a {ref.role} must be a {wanted}, not a {target.type}",
                        )

            links = [*item.sets_up, *item.pays_off]
            if links and item.kind is PlanKind.THREAD:
                blocking(
                    index, "invalid_plan_thread_link", "a thread cannot open or resolve a thread"
                )
            if len(links) > MAX_THREAD_LINKS:
                blocking(index, "invalid_plan_thread_link", "a plan item names too many threads")
            for thread_id in links:
                thread = await plan(thread_id)
                if thread is None or not thread.is_active or thread.kind is not PlanKind.THREAD:
                    blocking(
                        index, "unknown_plan_thread", f"{thread_id!r} is not a thread in the plan"
                    )

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
                        # Marks (tags, importance) sit beside the details, not in the type.
                        model.model_validate(details(operation.attributes))
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
                            model.model_validate(details(attributes))
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
                timed = (
                    operation.valid_from is not None
                    or operation.valid_until is not None
                    or not is_unknown(operation.starts)
                    or not is_unknown(operation.ends)
                )
                if spec is not None and not spec.temporal_allowed and timed:
                    blocking(
                        index,
                        "invalid_temporal_fact",
                        f"predicate {spec.predicate!r} does not allow a world-time interval",
                    )
                drafted_fact = Fact(
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
                    starts=operation.starts,
                    ends=operation.ends,
                    visibility=operation.visibility or FactVisibility.ORDINARY,
                )
                await check_span(index, drafted_fact)
                if spec is not None and spec.cardinality is Cardinality.ONE:
                    await check_one_at_a_time(index, drafted_fact)
                facts[operation.id] = drafted_fact
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
                    await forget_open(fact_current)
                    facts[operation.id] = fact_current.model_copy(
                        update={
                            "status": FactStatus.CLOSED,
                            "closed_revision": context.head_revision + 1,
                        }
                    )
            elif isinstance(operation, AmendFactOp):
                fact_current = await fact(operation.id)
                if fact_current is None:
                    blocking(index, "unknown_fact", f"fact {operation.id!r} does not exist")
                elif (
                    fact_current.status is FactStatus.CLOSED
                    or fact_current.closed_revision is not None
                ):
                    blocking(index, "closed_fact", f"fact {operation.id!r} is no longer true")
                else:
                    amended = fact_current.model_copy(
                        update={
                            "starts": fact_current.starts
                            if operation.starts is None
                            else operation.starts,
                            "ends": fact_current.ends if operation.ends is None else operation.ends,
                            "visibility": operation.visibility or fact_current.visibility,
                        }
                    )
                    spec = context.predicate_specs.get(
                        amended.proposition.predicate
                    ) or CORE_PREDICATES.get(amended.proposition.predicate)
                    if (
                        spec is not None
                        and not spec.temporal_allowed
                        and (not is_unknown(amended.starts) or not is_unknown(amended.ends))
                    ):
                        blocking(
                            index,
                            "invalid_temporal_fact",
                            f"predicate {spec.predicate!r} does not allow a world-time interval",
                        )
                    await check_span(index, amended)
                    if spec is not None and spec.cardinality is Cardinality.ONE:
                        await forget_open(fact_current)
                        await check_one_at_a_time(index, amended)
                    facts[operation.id] = amended
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
                    when=operation.when,
                    provenance_id="pending",
                    created_revision=context.head_revision + 1,
                )
                await check_when(index, operation.id, operation.when)
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
                            "when": event_current.when
                            if operation.when is None
                            else operation.when,
                        }
                    )
                    if operation.when is not None:
                        await check_when(index, operation.id, operation.when)
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
            elif isinstance(operation, CreatePlanItemOp):
                if operation.id in created_record_ids or await plan(operation.id) is not None:
                    blocking(index, "duplicate_id", f"record id {operation.id!r} already exists")
                    continue
                created_record_ids.add(operation.id)
                drafted = plan_item_from_create(
                    operation,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    branch_id=context.branch_id,
                    provenance_id="pending",
                    revision=context.head_revision + 1,
                )
                await validate_plan_item(index, drafted)
                plans[operation.id] = drafted
            elif isinstance(operation, UpdatePlanItemOp):
                plan_current = await plan(operation.id)
                if plan_current is None:
                    blocking(
                        index, "unknown_plan_item", f"plan item {operation.id!r} does not exist"
                    )
                elif not plan_current.is_active:
                    blocking(index, "retired_plan_item", f"plan item {operation.id!r} is removed")
                elif operation.only_fills and not _only_fills(plan_current, operation):
                    blocking(
                        index,
                        "plan_changed_since",
                        "The plan for this scene has changed since this was suggested, and "
                        "approving it would undo that. Ask for a new suggestion instead.",
                    )
                else:
                    changed = apply_plan_update(plan_current, operation)
                    await validate_plan_item(index, changed, plan_current)
                    plans[operation.id] = changed
            elif isinstance(operation, RetirePlanItemOp):
                plan_current = await plan(operation.id)
                if plan_current is None:
                    blocking(
                        index, "unknown_plan_item", f"plan item {operation.id!r} does not exist"
                    )
                elif not plan_current.is_active:
                    blocking(
                        index, "retired_plan_item", f"plan item {operation.id!r} is already removed"
                    )
                else:
                    # Stored children, as this set of changes leaves them, plus any it adds.
                    child_ids = set(await context.reader.plan_child_ids(operation.id))
                    child_ids.update(
                        key
                        for key, value in plans.items()
                        if value is not None and value.parent_id == operation.id
                    )
                    remaining = [
                        key
                        for key in sorted(child_ids)
                        if (child := await plan(key)) is not None
                        and child.is_active
                        and child.parent_id == operation.id
                    ]
                    if remaining:
                        blocking(
                            index,
                            "plan_item_has_parts",
                            f"plan item {operation.id!r} still has {len(remaining)} part(s) under it",
                        )
                    plans[operation.id] = plan_current.model_copy(
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
                if operation.target_record_type == "plan_item":
                    # A picture shown on a scene (AST-EXP-018); only scenes take pictures.
                    target_plan = await plan(operation.target_id)
                    if target_plan is None:
                        blocking(
                            index,
                            "unknown_reference",
                            f"unknown link target {operation.target_id!r}",
                        )
                    elif not target_plan.is_active:
                        blocking(
                            index,
                            "retired_reference",
                            f"link target {operation.target_id!r} is retired",
                        )
                    elif target_plan.kind != PlanKind.SCENE:
                        blocking(
                            index,
                            "unsupported_target",
                            "only a scene of the plan can show a picture",
                        )
                elif operation.target_record_type != "entity":
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
