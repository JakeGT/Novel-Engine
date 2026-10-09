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

from backend.domain.canon.events import EventParticipant
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.canon.revisions import OriginKind

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


class AssertFactOp(_Operation):
    op: Literal["assert_fact"] = "assert_fact"
    proposition: Proposition
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class CloseFactOp(_Operation):
    op: Literal["close_fact"] = "close_fact"


class AssertBeliefOp(_Operation):
    op: Literal["assert_belief"] = "assert_belief"
    holder_entity_id: str
    proposition: Proposition
    confidence: float = 1.0
    source_ref: str | None = None


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


class AmendEventOp(_Operation):
    op: Literal["amend_event"] = "amend_event"
    name: str | None = None
    world_position: str | None = None


class RetireEventOp(_Operation):
    op: Literal["retire_event"] = "retire_event"


class LinkAssetOp(_Operation):
    op: Literal["link_asset"] = "link_asset"
    asset_id: str
    asset_version_id: str | None = None
    target_record_type: str = "entity"
    target_id: str
    role: str = "EVIDENCE_FOR"


class RetireAssetLinkOp(_Operation):
    op: Literal["retire_asset_link"] = "retire_asset_link"


CanonOperation = Annotated[
    CreateEntityOp
    | UpdateEntityOp
    | RetireEntityOp
    | AssertFactOp
    | CloseFactOp
    | AssertBeliefOp
    | CloseBeliefOp
    | CreateEventOp
    | AmendEventOp
    | RetireEventOp
    | LinkAssetOp
    | RetireAssetLinkOp,
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
        return [
            *(p.entity_id for p in operation.participants),
            *operation.location_ids,
            *operation.cause_ids,
            *operation.outcome_ids,
        ]
    if isinstance(operation, LinkAssetOp):
        refs = [operation.asset_id, operation.target_id]
        if operation.asset_version_id is not None:
            refs.append(operation.asset_version_id)
        return refs
    return []


def _rewrite(operation: CanonOperation, mapping: dict[str, str]) -> CanonOperation:
    def resolve(value: str) -> str:
        if is_temporary_id(value):
            if value not in mapping:
                raise UnresolvedReferenceError(f"unresolved temporary id {value!r}")
            return mapping[value]
        return value

    if isinstance(operation, (UpdateEntityOp, RetireEntityOp)):
        return operation.model_copy(update={"id": resolve(operation.id)})
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
            }
        )
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
_CANONICAL_OMIT_WHEN_UNSET = ("evidence", "aliases", "external_ids")


def canonical_operation(operation: CanonOperation) -> dict[str, Any]:
    """The checksum-stable serialization of one operation."""
    payload = operation.model_dump(mode="json")
    for key in _CANONICAL_OMIT_WHEN_UNSET:
        if key in payload and not payload[key]:
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
    "AssertBeliefOp",
    "AssertFactOp",
    "CanonOperation",
    "CloseBeliefOp",
    "CloseFactOp",
    "CreateEntityOp",
    "CreateEventOp",
    "EvidenceRef",
    "LinkAssetOp",
    "OperationValidationError",
    "OriginKind",
    "RetireAssetLinkOp",
    "RetireEntityOp",
    "RetireEventOp",
    "TEMP_ID_PREFIX",
    "UnresolvedReferenceError",
    "UpdateEntityOp",
    "canonical_operation",
    "canonicalize_operations",
    "is_temporary_id",
    "operation_reference_ids",
    "operations_checksum",
    "parse_operation",
    "prop_subject",
    "resolve_temporary_ids",
]
