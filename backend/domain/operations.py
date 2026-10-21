"""Typed, ordered mutation operations (ADR-011).

A mutation set is an ordered list of typed operations. Objects created in the same
set carry temporary ids (``tmp:`` prefix) that are resolved deterministically to
durable ids exactly once; references may target existing objects or objects
created earlier in the same set. Operations are the only thing the commit saga and
the graph writer interpret; transport DTOs never leak into this layer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from backend.domain.canon.assertions import FactVisibility
from backend.domain.canon.events import EventClaim, EventParticipant
from backend.domain.canon.plans import PlanItem, PlanKind, PlanRef
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.canon.revisions import OriginKind
from backend.domain.scene_intent import INTENT_KEY
from backend.domain.threads import thread_advances
from backend.domain.worldtime import WorldTime

TEMP_ID_PREFIX = "tmp:"
_TEMP_ID = re.compile(r"^tmp:[A-Za-z0-9_\-]+$")


class OperationValidationError(RuntimeError):
    """A mutation set failed structural validation."""


class UnresolvedReferenceError(OperationValidationError):
    """An operation referenced a temporary id that the set never defines."""


def is_temporary_id(value: str) -> bool:
    return bool(_TEMP_ID.match(value))


MAX_EVIDENCE_PER_OPERATION = 10

# Opaque identifier shapes only: evidence points at a source artefact and a bounded
# location inside it. Prose, captions, or media content can never be pasted here.
_ARTEFACT_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,199}$"
_EVIDENCE_LOCATION = r"^[A-Za-z0-9_.:,=\-]{1,120}$"


class EvidenceRef(BaseModel):
    """A bounded pointer to the evidence behind an operation; never copied content."""

    model_config = ConfigDict(extra="forbid")

    source_artefact_id: str = Field(pattern=_ARTEFACT_ID)
    location: str | None = Field(default=None, pattern=_EVIDENCE_LOCATION)


class _Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # durable or temporary target id
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=MAX_EVIDENCE_PER_OPERATION)


class CreateEntityOp(_Operation):
    op: Literal["create_entity"] = "create_entity"
    type: str
    name: str
    slug: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)


class UpdateEntityOp(_Operation):
    op: Literal["update_entity"] = "update_entity"
    name: str | None = None
    slug: str | None = None
    attributes: dict[str, Any] | None = None
    # ``None`` leaves the current value; a list/mapping replaces it wholesale.
    aliases: list[str] | None = None
    external_ids: dict[str, str] | None = None


class RetireEntityOp(_Operation):
    op: Literal["retire_entity"] = "retire_entity"
    # Set when the entry is retired because it was merged into another (AST-EXP-017):
    # the kept entry's id. Omitted while unset (ADR-015). Undoing such a retirement
    # brings the entry back (``restore_entity``).
    merged_into: str | None = None


class RestoreEntityOp(_Operation):
    """Bring a retired entry back into the story, as it was when it was retired.

    Proposed by undoing a merge; like every change it is reviewed and validated.
    """

    op: Literal["restore_entity"] = "restore_entity"


class MergeEntitiesOp(_Operation):
    """Merge one entry (``id``) into another (``into``), reviewed as one change.

    What a merge moves is worked out when it is kept, from the story as it is then
    (``backend/domain/merge.py``): facts and beliefs naming the merged entry are ended
    and stated again about the kept one, events, plan items and pictures point at the
    kept one, its names become the kept one's other names, and it is retired. The
    commit records those ordinary changes; this operation is what the author reviews.
    ``drop_facts`` and ``drop_beliefs`` are ended rather than moved (a value the kept
    entry already has for something that holds one at a time). ``name``, ``aliases``
    and ``attributes`` replace what is worked out for the kept entry when given.
    """

    op: Literal["merge_entities"] = "merge_entities"
    into: str
    name: str | None = None
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    drop_facts: list[str] = Field(default_factory=list, max_length=500)
    drop_beliefs: list[str] = Field(default_factory=list, max_length=500)


class AssertFactOp(_Operation):
    op: Literal["assert_fact"] = "assert_fact"
    proposition: Proposition
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    # Story time (ADR-020): when the fact starts and stops being true. Unset means
    # from the story's start and until its end.
    starts: WorldTime | None = None
    ends: WorldTime | None = None
    # Unset is ordinary: known to whoever holds it, with nothing more said.
    visibility: FactVisibility | None = None
    # Why this fact is proposed, in plain words (a claim made a fact); proposal only.
    why: str | None = Field(default=None, max_length=300)


class CloseFactOp(_Operation):
    op: Literal["close_fact"] = "close_fact"
    # Why this fact is proposed to end, in plain words (a claim made a fact); proposal only.
    why: str | None = Field(default=None, max_length=300)


class AmendFactOp(_Operation):
    """Say when a fact is true, or who may know it. ``None`` leaves a field as it is."""

    op: Literal["amend_fact"] = "amend_fact"
    starts: WorldTime | None = None
    ends: WorldTime | None = None
    visibility: FactVisibility | None = None


class AssertBeliefOp(_Operation):
    op: Literal["assert_belief"] = "assert_belief"
    holder_entity_id: str
    proposition: Proposition
    confidence: float = 1.0
    source_ref: str | None = None
    # Why this belief is proposed, in plain words (KNW-006); kept with the proposal only.
    why: str | None = Field(default=None, max_length=300)


class CloseBeliefOp(_Operation):
    op: Literal["close_belief"] = "close_belief"


class CreateEventOp(_Operation):
    op: Literal["create_event"] = "create_event"
    kind: str
    name: str
    participants: list[EventParticipant] = Field(default_factory=list)
    location_ids: list[str] = Field(default_factory=list)
    cause_ids: list[str] = Field(default_factory=list)
    outcome_ids: list[str] = Field(default_factory=list)
    world_position: str | None = None
    # Story time (ADR-020). ``world_position`` stays the author's free words.
    when: WorldTime | None = None
    # What is said, on an event of kind "claim" (KNW-005). Omitted while unset.
    claim: EventClaim | None = None
    # The scene it happens in, and what an inference was worked out from (KNW-002).
    # Both omitted while unset.
    scene_id: str | None = Field(default=None, max_length=200)
    premises: list[str] = Field(default_factory=list, max_length=12)


class AmendEventOp(_Operation):
    op: Literal["amend_event"] = "amend_event"
    name: str | None = None
    world_position: str | None = None
    # ``None`` leaves the time as it is; an unknown time clears it.
    when: WorldTime | None = None
    # Who takes part and where, replaced wholesale; ``None`` leaves them as they are.
    # Used by a merge (AST-EXP-017). Omitted while unset (ADR-015, per operation).
    participants: list[EventParticipant] | None = None
    location_ids: list[str] | None = None
    # What causes it and what follows from it, replaced wholesale the same way.
    cause_ids: list[str] | None = None
    outcome_ids: list[str] | None = None


class RetireEventOp(_Operation):
    op: Literal["retire_event"] = "retire_event"


class LinkAssetOp(_Operation):
    op: Literal["link_asset"] = "link_asset"
    asset_id: str
    asset_version_id: str | None = None
    target_record_type: str = "entity"
    target_id: str
    role: str = "EVIDENCE_FOR"
    # AST-004, all optional and omitted while unset (ADR-015): a secret link and who
    # knows what the picture shows, and a story time from which the link applies.
    visibility: str | None = None
    known_by: list[str] = Field(default_factory=list)
    applies_from: dict[str, Any] | None = None


class RetireAssetLinkOp(_Operation):
    op: Literal["retire_asset_link"] = "retire_asset_link"


class CreatePlanItemOp(_Operation):
    """Add a story, arc, beat, scene or thread to the plan. Intent, never fact."""

    op: Literal["create_plan_item"] = "create_plan_item"
    kind: PlanKind
    title: str
    summary: str = ""
    parent_id: str | None = None
    position: int = 0
    refs: list[PlanRef] = Field(default_factory=list)
    sets_up: list[str] = Field(default_factory=list)
    pays_off: list[str] = Field(default_factory=list)
    details: dict[str, str] = Field(default_factory=dict)


class PlanBase(BaseModel):
    """The parts of a plan item a suggestion read, as they were when it was made (ADR-023).

    Kept on an ``only_fills`` change so approving it can tell whether the author has
    changed any part the change would replace since: added or removed someone, a thread
    it opens, settles or moves along.
    """

    model_config = ConfigDict(extra="forbid")

    refs: list[PlanRef] = Field(default_factory=list)
    sets_up: list[str] = Field(default_factory=list)
    pays_off: list[str] = Field(default_factory=list)
    advances: list[str] = Field(default_factory=list)
    # The scene's intent as stored then ("" for none), so a part the author has since
    # cleared or changed is never put back. ``None`` on older suggestions.
    intent: str | None = None
    # Where the item stood among its siblings, on a change that moves it to make room
    # (a bridge scene): approving is refused if the order has changed since.
    position: int | None = None

    @classmethod
    def of(cls, item: PlanItem) -> PlanBase:
        return cls(
            refs=list(item.refs),
            sets_up=list(item.sets_up),
            pays_off=list(item.pays_off),
            advances=thread_advances(item.details),
            intent=item.details.get(INTENT_KEY, ""),
        )


class UpdatePlanItemOp(_Operation):
    op: Literal["update_plan_item"] = "update_plan_item"
    title: str | None = None
    summary: str | None = None
    # ``None`` leaves the parent as it is; ``detach`` removes it.
    parent_id: str | None = None
    detach: bool = False
    position: int | None = None
    # ``None`` leaves the current value; a list or mapping replaces it wholesale.
    refs: list[PlanRef] | None = None
    sets_up: list[str] | None = None
    pays_off: list[str] | None = None
    details: dict[str, str] | None = None
    # Set by the assistant's suggestions (ADR-023): the change may only fill in what is empty
    # and add to what is there, and never moves the item. If the author has since written a
    # summary, chosen a viewpoint or place, or changed anything the change would replace
    # (added or removed someone, or a thread step), approving it is refused rather than
    # undoing their work. ``base`` is what the suggestion was made from; without it (older
    # suggestions) only additions and removals the change would drop are caught.
    only_fills: bool = False
    base: PlanBase | None = None


class RetirePlanItemOp(_Operation):
    op: Literal["retire_plan_item"] = "retire_plan_item"


CanonOperation = Annotated[
    CreateEntityOp
    | UpdateEntityOp
    | RetireEntityOp
    | RestoreEntityOp
    | MergeEntitiesOp
    | AssertFactOp
    | CloseFactOp
    | AmendFactOp
    | AssertBeliefOp
    | CloseBeliefOp
    | CreateEventOp
    | AmendEventOp
    | RetireEventOp
    | LinkAssetOp
    | RetireAssetLinkOp
    | CreatePlanItemOp
    | UpdatePlanItemOp
    | RetirePlanItemOp,
    Field(discriminator="op"),
]

_OPERATION_ADAPTER: TypeAdapter[CanonOperation] = TypeAdapter(CanonOperation)

# Which canon id kind a temporary object id resolves to, by creation operation.
_TEMP_KIND: dict[str, str] = {
    "create_entity": "entity",
    "assert_fact": "fact",
    "assert_belief": "belief",
    "create_event": "event",
    "link_asset": "asset_link",
    "create_plan_item": "plan_item",
}


def operation_reference_ids(operation: CanonOperation) -> list[str]:
    """Return every id an operation references (not its own target id)."""
    if isinstance(operation, (AssertFactOp,)):
        refs = [operation.proposition.subject_id]
        obj = operation.proposition.object
        if isinstance(obj, EntityRefObject):
            refs.append(obj.entity_id)
        return refs
    if isinstance(operation, AssertBeliefOp):
        refs = [operation.holder_entity_id, operation.proposition.subject_id]
        obj = operation.proposition.object
        if isinstance(obj, EntityRefObject):
            refs.append(obj.entity_id)
        return refs
    if isinstance(operation, CreateEventOp):
        claimed: list[str] = []
        if operation.claim is not None:
            claimed.append(operation.claim.proposition.subject_id)
            obj = operation.claim.proposition.object
            if isinstance(obj, EntityRefObject):
                claimed.append(obj.entity_id)
        return [
            *(p.entity_id for p in operation.participants),
            *operation.location_ids,
            *operation.cause_ids,
            *operation.outcome_ids,
            *operation.premises,
            *claimed,
        ]
    if isinstance(operation, LinkAssetOp):
        refs = [operation.asset_id, operation.target_id, *operation.known_by]
        if operation.asset_version_id is not None:
            refs.append(operation.asset_version_id)
        return refs
    if isinstance(operation, MergeEntitiesOp):
        return [operation.into]
    if isinstance(operation, RetireEntityOp) and operation.merged_into:
        return [operation.merged_into]
    if isinstance(operation, AmendEventOp):
        return [
            *(p.entity_id for p in operation.participants or []),
            *(operation.location_ids or []),
            *(operation.cause_ids or []),
            *(operation.outcome_ids or []),
        ]
    if isinstance(operation, CreatePlanItemOp | UpdatePlanItemOp):
        return [
            *([operation.parent_id] if operation.parent_id else []),
            *(ref.entity_id for ref in operation.refs or []),
            *(operation.sets_up or []),
            *(operation.pays_off or []),
        ]
    return []


def _rewrite(operation: CanonOperation, mapping: dict[str, str]) -> CanonOperation:
    def resolve(value: str) -> str:
        if is_temporary_id(value):
            if value not in mapping:
                raise UnresolvedReferenceError(f"unresolved temporary id {value!r}")
            return mapping[value]
        return value

    if isinstance(operation, (UpdateEntityOp, RestoreEntityOp)):
        return operation.model_copy(update={"id": resolve(operation.id)})
    if isinstance(operation, RetireEntityOp):
        return operation.model_copy(
            update={
                "id": resolve(operation.id),
                "merged_into": (
                    resolve(operation.merged_into) if operation.merged_into is not None else None
                ),
            }
        )
    if isinstance(operation, MergeEntitiesOp):
        return operation.model_copy(
            update={"id": resolve(operation.id), "into": resolve(operation.into)}
        )
    if isinstance(operation, AmendEventOp):
        return operation.model_copy(
            update={
                "id": resolve(operation.id),
                "participants": (
                    [
                        p.model_copy(update={"entity_id": resolve(p.entity_id)})
                        for p in operation.participants
                    ]
                    if operation.participants is not None
                    else None
                ),
                "location_ids": (
                    [resolve(x) for x in operation.location_ids]
                    if operation.location_ids is not None
                    else None
                ),
                "cause_ids": (
                    [resolve(x) for x in operation.cause_ids]
                    if operation.cause_ids is not None
                    else None
                ),
                "outcome_ids": (
                    [resolve(x) for x in operation.outcome_ids]
                    if operation.outcome_ids is not None
                    else None
                ),
            }
        )
    if isinstance(operation, AssertFactOp):
        prop = _rewrite_proposition(operation.proposition, resolve)
        return operation.model_copy(update={"id": resolve(operation.id), "proposition": prop})
    if isinstance(operation, AssertBeliefOp):
        prop = _rewrite_proposition(operation.proposition, resolve)
        return operation.model_copy(
            update={
                "id": resolve(operation.id),
                "holder_entity_id": resolve(operation.holder_entity_id),
                "proposition": prop,
            }
        )
    if isinstance(operation, CloseBeliefOp):
        return operation.model_copy(update={"id": resolve(operation.id)})
    if isinstance(operation, CreateEventOp):
        return operation.model_copy(
            update={
                "id": resolve(operation.id),
                "participants": [
                    p.model_copy(update={"entity_id": resolve(p.entity_id)})
                    for p in operation.participants
                ],
                "location_ids": [resolve(x) for x in operation.location_ids],
                "cause_ids": [resolve(x) for x in operation.cause_ids],
                "outcome_ids": [resolve(x) for x in operation.outcome_ids],
                "premises": [resolve(x) for x in operation.premises],
                "claim": (
                    operation.claim.model_copy(
                        update={
                            "proposition": _rewrite_proposition(
                                operation.claim.proposition, resolve
                            )
                        }
                    )
                    if operation.claim is not None
                    else None
                ),
            }
        )
    if isinstance(operation, LinkAssetOp):
        return operation.model_copy(
            update={
                "id": resolve(operation.id),
                "asset_id": resolve(operation.asset_id),
                "asset_version_id": (
                    resolve(operation.asset_version_id)
                    if operation.asset_version_id is not None
                    else None
                ),
                "target_id": resolve(operation.target_id),
                "known_by": [resolve(entity_id) for entity_id in operation.known_by],
            }
        )
    if isinstance(operation, CreatePlanItemOp | UpdatePlanItemOp):
        update: dict[str, Any] = {"id": resolve(operation.id)}
        if operation.parent_id:
            update["parent_id"] = resolve(operation.parent_id)
        if operation.refs is not None:
            update["refs"] = [
                ref.model_copy(update={"entity_id": resolve(ref.entity_id)})
                for ref in operation.refs
            ]
        if operation.sets_up is not None:
            update["sets_up"] = [resolve(x) for x in operation.sets_up]
        if operation.pays_off is not None:
            update["pays_off"] = [resolve(x) for x in operation.pays_off]
        return operation.model_copy(update=update)
    return operation.model_copy(update={"id": resolve(operation.id)})


def prop_subject(operation: AssertFactOp) -> str:
    return operation.proposition.subject_id


def _rewrite_proposition(proposition: Proposition, resolve: Callable[[str], str]) -> Proposition:
    subject_id = resolve(proposition.subject_id)
    obj = proposition.object
    if isinstance(obj, EntityRefObject):
        obj = obj.model_copy(update={"entity_id": resolve(obj.entity_id)})
    return proposition.model_copy(update={"subject_id": subject_id, "object": obj})


def resolve_temporary_ids(
    operations: Sequence[CanonOperation],
    *,
    new_id: Callable[[str, str], str],
) -> tuple[list[CanonOperation], dict[str, str]]:
    """Assign durable ids to every temporary object id, then rewrite references.

    ``new_id(kind, temporary_id)`` must return a durable id; deriving it from the
    mutation id keeps resolution deterministic across retries.
    """
    mapping: dict[str, str] = {}
    for operation in operations:
        if is_temporary_id(operation.id):
            kind = _TEMP_KIND.get(operation.op)
            if kind is None:
                raise UnresolvedReferenceError(
                    f"operation {operation.op!r} may not define a temporary id"
                )
            if operation.id not in mapping:
                mapping[operation.id] = new_id(kind, operation.id)

    # Validate that every temporary reference is defined before rewriting.
    defined = set(mapping)
    for operation in operations:
        for reference in operation_reference_ids(operation):
            if is_temporary_id(reference) and reference not in defined:
                raise UnresolvedReferenceError(f"unresolved temporary id {reference!r}")

    resolved = [_rewrite(operation, mapping) for operation in operations]
    return resolved, mapping


# Fields added after the first committed envelopes. They are omitted from the
# canonical form while unset so every earlier envelope keeps its exact checksum.
_CANONICAL_OMIT_WHEN_UNSET = (
    "evidence",
    "aliases",
    "external_ids",
    "when",
    "starts",
    "ends",
    "visibility",
    "only_fills",
    "base",
    "claim",
    "why",
    "scene_id",
    "premises",
    "known_by",
    "applies_from",
    "merged_into",
)

# Fields added to one kind of operation whose name another kind already uses with a
# value that must stay in the canonical form (``participants`` and ``location_ids`` of
# an event's creation, even when empty). Omitted only on that kind, and only while unset
# (``None``), so an explicit empty list on it is still recorded.
_CANONICAL_OMIT_WHEN_NONE_BY_OP: dict[str, tuple[str, ...]] = {
    "amend_event": ("participants", "location_ids", "cause_ids", "outcome_ids"),
}


def canonical_operation(operation: CanonOperation) -> dict[str, Any]:
    """The checksum-stable serialization of one operation."""
    payload = operation.model_dump(mode="json")
    for key in _CANONICAL_OMIT_WHEN_UNSET:
        if key in payload and not payload[key]:
            del payload[key]
    for key in _CANONICAL_OMIT_WHEN_NONE_BY_OP.get(operation.op, ()):
        if key in payload and payload[key] is None:
            del payload[key]
    return payload


def canonicalize_operations(operations: Sequence[CanonOperation]) -> str:
    payload = [canonical_operation(operation) for operation in operations]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def operations_checksum(operations: Sequence[CanonOperation]) -> str:
    return hashlib.sha256(canonicalize_operations(operations).encode("utf-8")).hexdigest()


def parse_operation(payload: dict[str, Any]) -> CanonOperation:
    return _OPERATION_ADAPTER.validate_python(payload)


__all__ = [
    "AmendEventOp",
    "AmendFactOp",
    "AssertBeliefOp",
    "AssertFactOp",
    "CanonOperation",
    "CloseBeliefOp",
    "CloseFactOp",
    "CreateEntityOp",
    "CreateEventOp",
    "CreatePlanItemOp",
    "EvidenceRef",
    "LinkAssetOp",
    "MergeEntitiesOp",
    "OperationValidationError",
    "OriginKind",
    "PlanBase",
    "RestoreEntityOp",
    "RetireAssetLinkOp",
    "RetireEntityOp",
    "RetireEventOp",
    "RetirePlanItemOp",
    "TEMP_ID_PREFIX",
    "UnresolvedReferenceError",
    "UpdateEntityOp",
    "UpdatePlanItemOp",
    "canonical_operation",
    "canonicalize_operations",
    "is_temporary_id",
    "operation_reference_ids",
    "operations_checksum",
    "parse_operation",
    "prop_subject",
    "resolve_temporary_ids",
]
