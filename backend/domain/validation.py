"""Authoritative canon validation (ADR-012/ADR-013).

All canon validation that can block a commit runs inside the canonical
PostgreSQL transaction, against the branch head being advanced. Findings are
split into permanently blocking integrity findings, blocking author-rule
findings (only when a rule is published as blocking and deterministically
evaluable), and advisory findings. There is no suppression path: the canonical
repository invokes this validator on every commit.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from backend.domain.arcs import PATH_KEY, arc_path, path_problem
from backend.domain.canon.assertions import Belief, Fact, FactStatus, FactVisibility
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import (
    CLAIM_KIND,
    CLAIM_NARRATOR,
    CLAIM_SPEAKER,
    INFERRED_KIND,
    Event,
)
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
    MergeEntitiesOp,
    RestoreEntityOp,
    RetireAssetLinkOp,
    RetireEntityOp,
    RetireEventOp,
    RetirePlanItemOp,
    UpdateEntityOp,
    UpdatePlanItemOp,
    operation_reference_ids,
)
from backend.domain.scene_intent import INTENT_KEY, scene_intent
from backend.domain.threads import ADVANCES_KEY, thread_advances
from backend.domain.worldtime import (
    Direction,
    Relative,
    Resolved,
    Span,
    TimeProblem,
    is_unknown,
    parse_world_time,
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

    async def current_beliefs(self, limit: int) -> list[Belief]:
        """The beliefs held now on this branch, at most ``limit`` (for story rules)."""
        ...

    async def live_events(self, limit: int) -> list[Event]:
        """The events not retired on this branch, at most ``limit`` (for story rules)."""
        ...

    async def live_plan_items(self, limit: int) -> list[PlanItem]:
        """The plan items not removed on this branch, at most ``limit`` (for a merge)."""
        ...

    async def live_asset_links(self, limit: int) -> list[AssetLink]:
        """The picture links not retired on this branch, at most ``limit`` (for a merge)."""
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


def _window_backwards(start: object, end: object) -> bool:
    """Whether a picture link's window certainly ends before (or as) it starts (AST-001).

    Only plain times are compared; one relative to an event is left to be placed later."""
    if start is None or end is None:
        return False
    first = resolve(parse_world_time(start), lambda _event: False).interval
    last = resolve(parse_world_time(end), lambda _event: False).interval
    if first is None or last is None:
        return False
    return last.hi <= first.lo


def _ref_keys(refs: Sequence[Any]) -> set[tuple[str, str]]:
    return {(ref.entity_id, ref.role) for ref in refs}


def _changed_since(current: PlanItem, change: UpdatePlanItemOp) -> bool:
    """Whether the author has changed, since the suggestion was made, a part it replaces."""
    base = change.base
    if base is None:
        return False
    return (
        (change.refs is not None and _ref_keys(current.refs) != _ref_keys(base.refs))
        or (change.sets_up is not None and set(current.sets_up) != set(base.sets_up))
        or (change.pays_off is not None and set(current.pays_off) != set(base.pays_off))
        or (
            change.details is not None
            and set(thread_advances(current.details)) != set(base.advances)
        )
    )


def _only_fills(current: PlanItem, change: UpdatePlanItemOp) -> bool:
    """Whether ``change`` only fills in what is empty and adds to what is there, on the
    item as the suggestion found it."""
    if change.title is not None:
        return False
    # A suggestion never moves the item.
    if change.parent_id is not None or change.detach or change.position is not None:
        return False
    if _changed_since(current, change):
        return False
    if change.details is not None and not _only_adds_steps(
        current.details, change.details, change.base.intent if change.base else None
    ):
        return False
    for held_threads, new_threads in (
        (current.sets_up, change.sets_up),
        (current.pays_off, change.pays_off),
    ):
        if new_threads is not None and not set(held_threads) <= set(new_threads):
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


def _advances_listed(raw: str) -> list[str] | None:
    """The thread ids stored as moved along, or None when the value is not a list of ids."""
    try:
        found = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(found, list) or not all(isinstance(t, str) and t for t in found):
        return None
    return list(dict.fromkeys(found))


def _only_adds_steps(
    current: dict[str, str], proposed: dict[str, str], base_intent: str | None = None
) -> bool:
    """Whether new details only add threads the item moves along (EXT-001) and fill in
    parts of the scene's intent left empty (STY-003), leaving every other detail exactly
    as the author has it."""
    filled = (ADVANCES_KEY, INTENT_KEY)
    others = {key: value for key, value in current.items() if key not in filled}
    if {key: value for key, value in proposed.items() if key not in filled} != others:
        return False
    if not _only_fills_intent(current, proposed, base_intent):
        return False
    return set(thread_advances(current)) <= set(thread_advances(proposed))


def _only_fills_intent(
    current: dict[str, str], proposed: dict[str, str], base: str | None = None
) -> bool:
    """Every part of the scene's intent the author has set is kept as it is, and none
    they have changed or cleared since the suggestion (``base``: the intent as stored
    then, "" for none) is put back."""
    if current.get(INTENT_KEY) == proposed.get(INTENT_KEY):
        return True
    held, offered = scene_intent(current), scene_intent(proposed)
    if base is not None and held != scene_intent({INTENT_KEY: base} if base else {}):
        return False
    if held is None:
        return True
    if offered is None:
        return False
    return (
        (not held.goal or held.goal == offered.goal)
        and (not held.obstacle or held.obstacle == offered.obstacle)
        and (held.outcome is None or held.outcome == offered.outcome)
    )


# A new type is checked against every current fact and belief, read at once; a story with
# more than this many of either is refused rather than checked in part (AST-EXP-017).
MAX_RETYPE_RECORDS = 100_000


def _retyped_details(error: PydanticValidationError, new_type: str) -> str:
    """Why an entry's details do not suit the type it is given, in plain words."""
    kind = new_type.lower()
    extra = [str(item["loc"][0]) for item in error.errors() if item["type"] == "extra_forbidden"]
    missing = [str(item["loc"][0]) for item in error.errors() if item["type"] == "missing"]
    wrong = [
        str(item["loc"][0])
        for item in error.errors()
        if item["type"] not in ("extra_forbidden", "missing") and item["loc"]
    ]

    def listed(keys: list[str]) -> str:
        return ", ".join(f"“{key}”" for key in dict.fromkeys(keys))

    said: list[str] = []
    if extra:
        said.append(
            f"a {kind} has no detail{'s' if len(extra) != 1 else ''} {listed(extra)}; remove "
            f"{'them' if len(extra) != 1 else 'it'} in the same change, or keep the type"
        )
    if missing:
        said.append(f"a {kind} needs {listed(missing)}; add it in the same change")
    if wrong:
        said.append(f"{listed(wrong)} does not suit a {kind}; change it in the same change")
    return "; ".join(said) or f"the entry's details do not suit a {kind}"


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
        # Entries given a new type in this set (AST-EXP-017): id -> (operation, new type).
        retyped: dict[str, tuple[int, str]] = {}

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

        def event_names(record: Event, entity_id: str) -> bool:
            return (
                any(p.entity_id == entity_id for p in record.participants)
                or entity_id in record.location_ids
                or entity_id in record.cause_ids
                or entity_id in record.outcome_ids
            )

        def proposition_names(proposition: Any, entity_id: str) -> bool:
            obj = proposition.object
            return proposition.subject_id == entity_id or (
                isinstance(obj, EntityRefObject) and obj.entity_id == entity_id
            )

        async def released_earlier(earlier: Sequence[CanonOperation], entity_id: str) -> int:
            """How many of the records now naming ``entity_id`` (as counted by
            ``count_active_references``) the changes before it in this set end or move."""
            released: set[tuple[str, str]] = set()
            for change in earlier:
                if isinstance(change, CloseFactOp):
                    stored_fact = await context.reader.get_fact(change.id)
                    if (
                        stored_fact is not None
                        and stored_fact.closed_revision is None
                        and proposition_names(stored_fact.proposition, entity_id)
                    ):
                        released.add(("fact", change.id))
                elif isinstance(change, CloseBeliefOp):
                    stored_belief = await context.reader.get_belief(change.id)
                    if (
                        stored_belief is not None
                        and stored_belief.closed_revision is None
                        and (
                            stored_belief.holder_entity_id == entity_id
                            or proposition_names(stored_belief.proposition, entity_id)
                        )
                    ):
                        released.add(("belief", change.id))
                elif isinstance(change, RetireAssetLinkOp):
                    stored_link = await context.reader.get_link(change.id)
                    if (
                        stored_link is not None
                        and stored_link.retired_revision is None
                        and stored_link.target_record_id == entity_id
                    ):
                        released.add(("asset_link", change.id))
                elif isinstance(change, AmendEventOp | RetireEventOp):
                    stored_event = await context.reader.get_event(change.id)
                    if (
                        stored_event is None
                        or stored_event.retired_revision is not None
                        or not event_names(stored_event, entity_id)
                    ):
                        continue
                    final = events.get(change.id, stored_event)
                    if (
                        final is None
                        or final.retired_revision is not None
                        or not event_names(final, entity_id)
                    ):
                        released.add(("event", change.id))
            return len(released)

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
            # The threads it moves along (EXT-001), checked where they changed.
            raw_advances = item.details.get(ADVANCES_KEY)
            if raw_advances and raw_advances != (previous.details if previous else {}).get(
                ADVANCES_KEY
            ):
                advancing = _advances_listed(raw_advances)
                held = set(thread_advances(previous.details)) if previous else set()
                if advancing is None:
                    blocking(
                        index,
                        "invalid_plan_thread_link",
                        "the threads a plan item moves along are malformed",
                    )
                    advancing = []
                elif advancing and item.kind is PlanKind.THREAD:
                    blocking(
                        index, "invalid_plan_thread_link", "a thread cannot move a thread along"
                    )
                elif len(advancing) > MAX_THREAD_LINKS:
                    blocking(
                        index,
                        "invalid_plan_thread_link",
                        "a plan item moves too many threads along",
                    )
                links = [*links, *(t for t in advancing if t not in held and t not in links)]
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

        async def validate_swapped(index: int, before: Any, after: Any) -> None:
            """A claim naming another entry in place of one it named: the new entry must be
            in the story and, unless it is of the same type as the one it replaces, fit."""
            spec = context.predicate_specs.get(after.predicate) or CORE_PREDICATES.get(
                after.predicate
            )
            if after.subject_id != before.subject_id:
                subject = await require_active_entity(index, after.subject_id, "subject")
                old = await entity(before.subject_id)
                if (
                    subject is not None
                    and spec is not None
                    and (old is None or old.type != subject.type)
                    and subject.type not in spec.subject_types
                ):
                    blocking(
                        index,
                        "invalid_proposition",
                        f"predicate {spec.predicate!r} does not accept subject type "
                        f"{subject.type!r}",
                    )
            old_target = (
                before.object.entity_id if isinstance(before.object, EntityRefObject) else None
            )
            if isinstance(after.object, EntityRefObject):
                if after.object.entity_id == old_target:
                    return
                if after.object.project_id != context.project_id:
                    blocking(index, "cross_project_reference", "entity reference crosses project")
                    return
                target = await require_active_entity(
                    index, after.object.entity_id, "object reference"
                )
                old = await entity(old_target) if old_target is not None else None
                if (
                    target is not None
                    and spec is not None
                    and spec.object_types
                    and (old is None or old.type != target.type)
                    and target.type not in spec.object_types
                ):
                    blocking(
                        index,
                        "invalid_proposition",
                        f"predicate {spec.predicate!r} does not accept target type {target.type!r}",
                    )
            elif after.object != before.object:
                await validate_proposition(index, after)

        # Entries a change in this batch names, so a merge does not leave one pointing
        # at the entry it retires (AST-EXP-017).
        named_in_batch: set[str] = set()
        for index, operation in enumerate(operations):
            if not 0.0 <= getattr(operation, "confidence", 1.0) <= 1.0:
                blocking(index, "invalid_confidence", "confidence must be between 0 and 1")
            await validate_evidence(index, operation)
            if isinstance(operation, RetireEntityOp) and operation.merged_into is not None:
                if operation.id in named_in_batch:
                    blocking(
                        index,
                        "merge_overlap",
                        "a change in this set names the entry being merged away; ask for it "
                        "again once the merge is kept",
                    )
            elif not isinstance(operation, (RetireEntityOp, MergeEntitiesOp)):
                named_in_batch.update(operation_reference_ids(operation))

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
                if current is not None and operation.type is not None:
                    # AST-EXP-017: a new type; what it holds is checked once the set is read.
                    if operation.type not in context.allowed_entity_types:
                        blocking(index, "unknown_type", f"unknown entity type {operation.type!r}")
                        continue
                    retyped[operation.id] = (index, operation.type)
                    current = current.model_copy(update={"type": operation.type})
                if current is not None:
                    attributes = (
                        current.attributes if operation.attributes is None else operation.attributes
                    )
                    model = context.entity_attribute_models.get(current.type)
                    if model is not None:
                        try:
                            model.model_validate(details(attributes))
                        except PydanticValidationError as error:
                            blocking(
                                index,
                                "invalid_attributes",
                                _retyped_details(error, current.type)
                                if operation.type is not None
                                else str(error),
                            )
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
                        check_name=(
                            operation.name is not None
                            and normalize_name(operation.name) != normalize_name(current.name)
                        )
                        or operation.type is not None,
                        check_slug=operation.slug is not None and operation.slug != current.slug,
                        check_external_ids=operation.external_ids is not None,
                    )
                    entities[operation.id] = updated
            elif isinstance(operation, RetireEntityOp):
                current = await require_active_entity(index, operation.id, "entity")
                if operation.merged_into is not None:
                    kept = await require_active_entity(index, operation.merged_into, "entity")
                    if kept is not None and current is not None and kept.type != current.type:
                        blocking(index, "invalid_merge", "only entries of one type are merged")
                if current is not None:
                    references = await context.reader.count_active_references(operation.id)
                    if operation.merged_into is not None:
                        # A merge moves what named the entry earlier in this same set;
                        # anything still naming it (a what-if brought back after the main
                        # story named it again) would be left pointing at a retired entry.
                        references -= await released_earlier(operations[:index], operation.id)
                        if references > 0:
                            blocking(
                                index,
                                "merge_leaves_references",
                                f"{references} record(s) still name the entry being merged "
                                "away; ask for the merge again so they move too",
                            )
                        references = 0
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
            elif isinstance(operation, RestoreEntityOp):
                current = await entity(operation.id)
                if current is None:
                    blocking(index, "unknown_reference", f"unknown entity {operation.id!r}")
                elif current.lifecycle is EntityLifecycle.ACTIVE:
                    blocking(index, "active_entity", f"entity {operation.id!r} is not retired")
                else:
                    restored = current.model_copy(
                        update={"lifecycle": EntityLifecycle.ACTIVE, "retired_revision": None}
                    )
                    # Coming back must not make two entries share a name or handle.
                    await validate_handles(
                        index,
                        operation.id,
                        restored.type,
                        name=restored.name,
                        slug=restored.slug,
                        aliases=restored.aliases,
                        external_ids=restored.external_ids,
                        check_name=True,
                        check_slug=restored.slug is not None,
                        check_external_ids=False,
                    )
                    entities[operation.id] = restored
            elif isinstance(operation, MergeEntitiesOp):
                # A merge reaches the validator only when it could not be worked out
                # into the changes it makes (backend/domain/merge.py): say why.
                merged = await require_active_entity(index, operation.id, "entity")
                kept = await require_active_entity(index, operation.into, "entity")
                if merged is not None and kept is not None:
                    if merged.id == kept.id:
                        blocking(index, "invalid_merge", "an entry cannot be merged with itself")
                    elif merged.type != kept.type:
                        blocking(index, "invalid_merge", "only entries of one type are merged")
                    elif (
                        sum(
                            1
                            for other in operations
                            if isinstance(other, MergeEntitiesOp) and other.into == operation.into
                        )
                        > 1
                    ):
                        blocking(
                            index,
                            "merge_overlap",
                            "only one entry can be merged into the same entry in a set of "
                            "changes; merge the others into it once this merge is kept",
                        )
                    else:
                        blocking(
                            index,
                            "merge_overlap",
                            "an entry is merged only once in a set of changes, and an entry "
                            "merged away cannot also be the one kept",
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
                if (operation.claim is not None) != (operation.kind == CLAIM_KIND):
                    blocking(
                        index,
                        "invalid_claim",
                        "only an event of kind 'claim' says something, and it must say what",
                    )
                if operation.claim is not None:
                    await validate_proposition(index, operation.claim.proposition)
                    if operation.claim.scene_id is not None:
                        scene = await plan(operation.claim.scene_id)
                        if scene is None or not scene.is_active:
                            blocking(index, "unknown_scene", "the claim's scene is not in the plan")
                        elif scene.kind is not PlanKind.SCENE:
                            blocking(index, "invalid_scene", "a claim is said in a scene")
                    speakers = [
                        p
                        for p in operation.participants
                        if p.role in (CLAIM_SPEAKER, CLAIM_NARRATOR)
                    ]
                    if len(speakers) > 1:
                        blocking(index, "invalid_claim", "a claim has at most one speaker")
                # Where it happens, and what an inference was worked out from (KNW-002).
                if operation.scene_id is not None:
                    scene = await plan(operation.scene_id)
                    if scene is None or not scene.is_active:
                        blocking(index, "unknown_scene", "the event's scene is not in the plan")
                    elif scene.kind is not PlanKind.SCENE:
                        blocking(index, "invalid_scene", "an event happens in a scene")
                if bool(operation.premises) != (operation.kind == INFERRED_KIND):
                    blocking(
                        index,
                        "invalid_inference",
                        "only an event of kind 'inferred' has premises, and it must have one",
                    )
                if len(set(operation.premises)) != len(operation.premises):
                    blocking(index, "invalid_inference", "each premise is named once")
                for premise in operation.premises:
                    found = await fact(premise) or await belief(premise)
                    if found is None:
                        blocking(
                            index,
                            "unknown_reference",
                            f"premise {premise!r} is not a fact or belief",
                        )
                    elif found.closed_revision is not None:
                        blocking(index, "retired_reference", f"premise {premise!r} is closed")
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
                    claim=operation.claim,
                    scene_id=operation.scene_id,
                    premises=list(operation.premises),
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
                    if operation.claim is not None:
                        if event_current.kind != CLAIM_KIND:
                            blocking(
                                index,
                                "invalid_claim",
                                "only an event of kind 'claim' says something",
                            )
                        said = event_current.claim
                        if said is not None and said.proposition.predicate == (
                            operation.claim.proposition.predicate
                        ):
                            # What was said is kept as said: only an entry it now names
                            # instead of another (a merge) is checked, so a claim about
                            # something since archived, or recorded before a new type,
                            # never blocks the change.
                            await validate_swapped(
                                index, said.proposition, operation.claim.proposition
                            )
                        else:
                            await validate_proposition(index, operation.claim.proposition)
                    for participant in operation.participants or []:
                        await require_active_entity(index, participant.entity_id, "participant")
                    for place in operation.location_ids or []:
                        await require_active_entity(index, place, "location")
                    for ref in (*(operation.cause_ids or []), *(operation.outcome_ids or [])):
                        referenced_entity = await entity(ref)
                        referenced_event = await event(ref)
                        if referenced_entity is None and referenced_event is None:
                            blocking(index, "unknown_reference", f"unknown event reference {ref!r}")
                        elif (
                            referenced_entity is not None and not referenced_entity.is_active
                        ) or (
                            referenced_event is not None
                            and referenced_event.retired_revision is not None
                        ):
                            blocking(
                                index, "retired_reference", f"event reference {ref!r} is retired"
                            )
                    events[operation.id] = event_current.model_copy(
                        update={
                            "participants": event_current.participants
                            if operation.participants is None
                            else list(operation.participants),
                            "location_ids": event_current.location_ids
                            if operation.location_ids is None
                            else list(operation.location_ids),
                            "cause_ids": event_current.cause_ids
                            if operation.cause_ids is None
                            else list(operation.cause_ids),
                            "outcome_ids": event_current.outcome_ids
                            if operation.outcome_ids is None
                            else list(operation.outcome_ids),
                            "claim": event_current.claim
                            if operation.claim is None
                            else operation.claim,
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
                elif (
                    operation.position is not None
                    and operation.base is not None
                    and operation.base.position is not None
                    and plan_current.position != operation.base.position
                ):
                    blocking(
                        index,
                        "plan_changed_since",
                        "The order of the plan has changed since this was suggested, so the "
                        "new scenes would not land where they were meant to. Ask for a new "
                        "suggestion instead.",
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
                # AST-004: who knows a secret picture, and from when a link applies.
                if operation.visibility not in (None, "open", "secret"):
                    blocking(
                        index,
                        "invalid_visibility",
                        f"unknown link visibility {operation.visibility!r}",
                    )
                if operation.known_by and operation.visibility != "secret":
                    blocking(
                        index,
                        "invalid_known_by",
                        "only a secret picture names who knows what it shows",
                    )
                for known in dict.fromkeys(operation.known_by):
                    await require_active_entity(index, known, "character who knows")
                if operation.applies_from is not None and (
                    parse_world_time(operation.applies_from) is None
                ):
                    blocking(
                        index, "invalid_time", "the story time the link applies from is not valid"
                    )
                if operation.applies_until is not None and (
                    parse_world_time(operation.applies_until) is None
                ):
                    blocking(
                        index, "invalid_time", "the story time the link applies until is not valid"
                    )
                if operation.shows_version and operation.asset_version_id is None:
                    blocking(
                        index,
                        "invalid_version",
                        "a link that shows one version must name that version",
                    )
                if _window_backwards(operation.applies_from, operation.applies_until):
                    blocking(
                        index,
                        "invalid_time",
                        "the picture would stop applying before it starts",
                    )
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
                    visibility=operation.visibility or "open",
                    known_by=list(operation.known_by),
                    applies_from=operation.applies_from,
                    applies_until=operation.applies_until,
                    shows_version=bool(operation.shows_version),
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

        if retyped:
            await self._check_retyped(retyped, facts, beliefs, context, blocking)
        return DomainValidationReport(findings=tuple(findings))

    @staticmethod
    async def _check_retyped(
        retyped: dict[str, tuple[int, str]],
        batch_facts: dict[str, Fact | None],
        batch_beliefs: dict[str, Belief | None],
        context: ValidationContext,
        blocking: Any,
    ) -> None:
        """Every current fact and belief about an entry given a new type must still fit
        it (AST-EXP-017), counting what the same set ends or states. A story with more
        than can be read at once is refused in plain words rather than checked in part."""
        stored_facts = await context.reader.asserted_facts(MAX_RETYPE_RECORDS + 1)
        stored_beliefs = await context.reader.current_beliefs(MAX_RETYPE_RECORDS + 1)
        if len(stored_facts) > MAX_RETYPE_RECORDS or len(stored_beliefs) > MAX_RETYPE_RECORDS:
            first = min(position for position, _ in retyped.values())
            blocking(
                first,
                "story_too_large",
                f"the story has more than {MAX_RETYPE_RECORDS:,} current facts or beliefs, too "
                "many to check a new type against at once; keep the type",
            )
            return
        facts = {fact.id: fact for fact in stored_facts}
        facts.update({key: value for key, value in batch_facts.items() if value is not None})
        beliefs = {belief.id: belief for belief in stored_beliefs}
        beliefs.update({key: value for key, value in batch_beliefs.items() if value is not None})

        def spec_of(predicate: str) -> PredicateSpec | None:
            return context.predicate_specs.get(predicate) or CORE_PREDICATES.get(predicate)

        def check(proposition: Any, what: str) -> None:
            spec = spec_of(proposition.predicate)
            if spec is None:
                return
            words = spec.predicate.replace("_", " ").lower()
            subject = retyped.get(proposition.subject_id)
            if subject is not None and subject[1] not in spec.subject_types:
                blocking(
                    subject[0],
                    "type_does_not_fit",
                    f"a {subject[1].lower()} cannot have the {what} “{words}”; end it "
                    "in the same change, or keep the type",
                )
            obj = proposition.object
            target = obj.entity_id if isinstance(obj, EntityRefObject) else None
            pointed = retyped.get(target) if target is not None else None
            if pointed is not None and spec.object_types and pointed[1] not in spec.object_types:
                blocking(
                    pointed[0],
                    "type_does_not_fit",
                    f"the {what} “{words}” cannot point to a {pointed[1].lower()}; end "
                    "it in the same change, or keep the type",
                )

        for fact in facts.values():
            if fact.closed_revision is None:
                check(fact.proposition, "fact")
        for belief in beliefs.values():
            if belief.closed_revision is not None:
                continue
            holder = retyped.get(belief.holder_entity_id)
            if holder is not None and holder[1] != "Character":
                blocking(
                    holder[0],
                    "type_does_not_fit",
                    f"only a character holds beliefs, and a {holder[1].lower()} is not one; end "
                    "them in the same change, or keep the type",
                )
            check(belief.proposition, "belief")


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
