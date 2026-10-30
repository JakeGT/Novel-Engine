"""Checks a set of proposed changes against the story as it stands, before review.

The structural validator says whether changes *can* be applied. This says what an
author would want to know before deciding: that an entry seems to exist already,
that a fact is already recorded. Findings are advisory and worded for an author,
with names rather than ids. Nothing here calls a model, and nothing here blocks.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.naming import normalize_name
from backend.domain.operations import (
    AssertFactOp,
    CanonOperation,
    CreateEntityOp,
    UpdateEntityOp,
)

MAX_ENTITIES = 2000

# (index into the operations given, code, message)
Finding = tuple[int, str, str]


def _phrase(predicate: str) -> str:
    return predicate.replace("_", " ").lower()


def _kind(entity_type: str) -> str:
    return entity_type.lower()


def proposed_names(operations: Sequence[CanonOperation]) -> dict[str, str]:
    """Names of the entries a set of changes would add, by their temporary ids."""
    return {op.id: op.name for op in operations if isinstance(op, CreateEntityOp)}


def plain_message(code: str, operation: CanonOperation | None, names: dict[str, str]) -> str | None:
    """An author's wording for the structural findings they are most likely to meet."""
    if code == "cardinality_conflict" and isinstance(operation, AssertFactOp):
        subject = names.get(operation.proposition.subject_id, "This entry")
        return (
            f"{subject} can be “{_phrase(operation.proposition.predicate)}” only one thing at "
            "a time, and already is. Skip this change, or end the current fact first."
        )
    if code == "name_collision" and isinstance(operation, CreateEntityOp | UpdateEntityOp):
        kind = _kind(operation.type) if isinstance(operation, CreateEntityOp) else "entry"
        return (
            f"You already have a {kind} called “{operation.name}”. Two entries can share a "
            "name, so this can still be added; check it is not the same one."
        )
    if code == "stale_head":
        return (
            "Your story has changed since these were suggested. Update them to the latest "
            "version, then look them over again."
        )
    if code == "empty_mutation":
        return "Every change is skipped, so there is nothing to add."
    return None


async def consistency_findings(
    operations: Sequence[CanonOperation],
    *,
    repository: Any,
    workspace_id: str,
    project_id: str,
) -> list[Finding]:
    entities: list[CanonEntity] = list(
        await repository.list_entities(
            workspace_id=workspace_id,
            project_id=project_id,
            include_retired=True,
            limit=MAX_ENTITIES,
        )
    )
    by_label: dict[str, list[CanonEntity]] = {}
    for entity in entities:
        for label in (entity.name, *entity.aliases):
            by_label.setdefault(normalize_name(label), []).append(entity)

    findings: list[Finding] = []
    drafted: dict[str, int] = {}
    for index, operation in enumerate(operations):
        if isinstance(operation, CreateEntityOp | UpdateEntityOp) and operation.name:
            key = normalize_name(operation.name)
            twins = [entity for entity in by_label.get(key, []) if entity.id != operation.id]
            own_type = (
                operation.type
                if isinstance(operation, CreateEntityOp)
                else next((e.type for e in entities if e.id == operation.id), None)
            )
            # A twin of the same type is already advised by the structural check.
            active = [e for e in twins if e.is_active and e.type != own_type]
            archived = [e for e in twins if not e.is_active]
            first = drafted.get(key)
            if isinstance(operation, CreateEntityOp):
                drafted.setdefault(key, index)
            if any(e.is_active for e in twins) and not active:
                continue
            if active:
                twin = active[0]
                message = (
                    f"You already have a {_kind(twin.type)} called “{twin.name}”. If this is "
                    "the same thing, skip this change."
                )
                findings.append((index, "possible_duplicate", message))
            elif archived:
                twin = archived[0]
                message = (
                    f"You archived a {_kind(twin.type)} called “{twin.name}”. If this is the "
                    "same one, you could restore it instead."
                )
                findings.append((index, "archived_twin", message))
            elif first is not None and isinstance(operation, CreateEntityOp):
                message = f"Another change here also adds something called “{operation.name}”."
                findings.append((index, "possible_duplicate", message))
        elif isinstance(operation, AssertFactOp):
            subject_id = operation.proposition.subject_id
            if subject_id.startswith("tmp:"):
                continue
            current = await repository.list_facts(
                workspace_id=workspace_id,
                project_id=project_id,
                subject_id=subject_id,
                predicate=operation.proposition.predicate,
                limit=200,
            )
            if any(fact.proposition.object == operation.proposition.object for fact in current):
                findings.append(
                    (
                        index,
                        "already_recorded",
                        "This is already recorded in your story, so adding it again changes "
                        "nothing. You can skip it.",
                    )
                )
    return findings


__all__ = ["Finding", "consistency_findings", "plain_message", "proposed_names"]
