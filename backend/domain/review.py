"""Mutation review policy: state machine, per-operation decisions, and impact gate.

Pure, storage-neutral rules shared by the mutation service, the PostgreSQL
commit transaction, and the in-memory test double, so every writer evaluates the
same review policy. Three things live here:

* the mutation-set state machine (legal transitions only; ``committed`` and
  ``rejected`` are terminal),
* the composition of the *effective* operation set from the current operation
  revisions and their per-operation decisions (rejected operations never reach
  canon), and
* the classification that decides whether a set needs a distinct human approval
  before it may commit (ADR-012 decision 1).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from backend.domain.canon.revisions import OriginKind
from backend.domain.journal import MutationOperation, MutationStatus, OperationDecision


class OperationDecisionKind(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class MutationIntent(StrEnum):
    EDIT = "edit"
    REVERT = "revert"


class ReviewReason(StrEnum):
    PROTECTED_ORIGIN = "protected_origin"
    DESTRUCTIVE_OPERATION = "destructive_operation"
    BULK_CHANGE = "bulk_change"
    CONFLICT_RESOLUTION = "conflict_resolution"


# Origins whose proposals may commit without a separate approval when low-impact.
DIRECT_COMMIT_ORIGINS = frozenset({OriginKind.AUTHOR.value, OriginKind.MIGRATION.value})

# Operations that remove an identity-bearing record from active canon. A merge retires
# the entry merged away and moves everything that named it (AST-EXP-017).
DESTRUCTIVE_OPERATIONS = frozenset({"retire_entity", "retire_event", "merge_entities"})

# More operations than this in one set is a bulk change needing explicit approval.
BULK_OPERATION_THRESHOLD = 25

LEGAL_TRANSITIONS: dict[MutationStatus, frozenset[MutationStatus]] = {
    MutationStatus.DRAFT: frozenset(
        {
            MutationStatus.VALIDATING,
            MutationStatus.NEEDS_REVIEW,
            MutationStatus.APPROVED,
            MutationStatus.REJECTED,
        }
    ),
    MutationStatus.VALIDATING: frozenset(
        {MutationStatus.DRAFT, MutationStatus.NEEDS_REVIEW, MutationStatus.REJECTED}
    ),
    MutationStatus.NEEDS_REVIEW: frozenset(
        {MutationStatus.DRAFT, MutationStatus.APPROVED, MutationStatus.REJECTED}
    ),
    MutationStatus.APPROVED: frozenset(
        {
            MutationStatus.NEEDS_REVIEW,
            MutationStatus.COMMITTING,
            MutationStatus.COMMITTED,
            MutationStatus.REJECTED,
        }
    ),
    MutationStatus.COMMITTING: frozenset(
        {MutationStatus.COMMITTED, MutationStatus.APPROVED, MutationStatus.RECOVERY_REQUIRED}
    ),
    MutationStatus.RECOVERY_REQUIRED: frozenset(
        {MutationStatus.COMMITTED, MutationStatus.APPROVED, MutationStatus.REJECTED}
    ),
    MutationStatus.COMMITTED: frozenset(),
    MutationStatus.REJECTED: frozenset(),
}

TERMINAL_STATUSES = frozenset({MutationStatus.COMMITTED, MutationStatus.REJECTED})


class IllegalTransition(RuntimeError):
    """A mutation-set status change is not permitted by the state machine."""

    def __init__(self, current: MutationStatus, target: MutationStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"mutation cannot move from {current.value!r} to {target.value!r}")


def can_transition(current: MutationStatus, target: MutationStatus) -> bool:
    return target in LEGAL_TRANSITIONS[current]


def ensure_transition(current: MutationStatus, target: MutationStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


def is_editable(status: MutationStatus) -> bool:
    """Operations and their decisions may change only before a terminal outcome."""
    return status not in TERMINAL_STATUSES and status is not MutationStatus.COMMITTING


def latest_operation_decisions(
    decisions: Iterable[OperationDecision],
    operations: Sequence[MutationOperation],
) -> dict[int, OperationDecision]:
    """Latest decision per ordinal that still applies to the operation's current revision.

    A decision recorded against a superseded operation revision is review
    evidence for that revision only; editing an operation invalidates it.
    """
    current_revision = {operation.ordinal: operation.revision for operation in operations}
    latest: dict[int, OperationDecision] = {}
    for decision in decisions:
        if current_revision.get(decision.ordinal) != decision.operation_revision:
            continue
        latest[decision.ordinal] = decision
    return latest


def effective_operations(
    operations: Sequence[MutationOperation],
    decisions: Iterable[OperationDecision],
) -> list[MutationOperation]:
    """Current operation revisions minus those whose latest decision is a rejection."""
    latest = latest_operation_decisions(decisions, operations)
    return [
        operation
        for operation in sorted(operations, key=lambda item: item.ordinal)
        if (
            operation.ordinal not in latest
            or latest[operation.ordinal].decision != OperationDecisionKind.REJECTED.value
        )
    ]


@dataclass(frozen=True)
class ReviewClassification:
    reasons: tuple[ReviewReason, ...] = ()

    @property
    def requires_review(self) -> bool:
        return bool(self.reasons)


def classify_review(
    *, origin: str, intent: str, operation_types: Sequence[str]
) -> ReviewClassification:
    """Decide whether a mutation set needs a distinct human approval before commit.

    Trusted migration replays (archive restore of already-accepted history) are
    exempt; every other origin is classified by origin, intent, and content.
    """
    if origin == OriginKind.MIGRATION.value:
        return ReviewClassification()
    reasons: list[ReviewReason] = []
    if origin not in DIRECT_COMMIT_ORIGINS:
        reasons.append(ReviewReason.PROTECTED_ORIGIN)
    if any(operation_type in DESTRUCTIVE_OPERATIONS for operation_type in operation_types):
        reasons.append(ReviewReason.DESTRUCTIVE_OPERATION)
    if len(operation_types) > BULK_OPERATION_THRESHOLD:
        reasons.append(ReviewReason.BULK_CHANGE)
    if intent == MutationIntent.REVERT.value:
        reasons.append(ReviewReason.CONFLICT_RESOLUTION)
    return ReviewClassification(reasons=tuple(reasons))


@dataclass(frozen=True)
class ReviewGateResult:
    """Outcome of the commit-time review gate; ``code`` is null when the gate passes."""

    code: str | None = None
    message: str = ""
    reasons: tuple[ReviewReason, ...] = ()

    @property
    def ok(self) -> bool:
        return self.code is None


def review_gate(
    *, origin: str, intent: str, status: MutationStatus, operation_types: Sequence[str]
) -> ReviewGateResult:
    """The non-bypassable review precondition evaluated inside the commit transaction."""
    if status is MutationStatus.REJECTED:
        return ReviewGateResult("review_rejected", "the proposal was rejected during review")
    if not operation_types:
        return ReviewGateResult("empty_mutation", "no accepted operations remain to commit")
    classification = classify_review(origin=origin, intent=intent, operation_types=operation_types)
    if status is MutationStatus.APPROVED:
        return ReviewGateResult(reasons=classification.reasons)
    if status is MutationStatus.NEEDS_REVIEW:
        return ReviewGateResult(
            "review_required",
            "the proposal is awaiting an explicit review decision",
            classification.reasons,
        )
    if classification.requires_review:
        names = ", ".join(reason.value for reason in classification.reasons)
        return ReviewGateResult(
            "review_required",
            f"this proposal requires an explicit human approval ({names})",
            classification.reasons,
        )
    if status is not MutationStatus.DRAFT:
        return ReviewGateResult(
            "review_required", f"mutation in state {status.value!r} cannot be committed"
        )
    return ReviewGateResult(reasons=classification.reasons)


__all__ = [
    "BULK_OPERATION_THRESHOLD",
    "DESTRUCTIVE_OPERATIONS",
    "DIRECT_COMMIT_ORIGINS",
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATUSES",
    "IllegalTransition",
    "MutationIntent",
    "OperationDecisionKind",
    "ReviewClassification",
    "ReviewGateResult",
    "ReviewReason",
    "can_transition",
    "classify_review",
    "effective_operations",
    "ensure_transition",
    "is_editable",
    "latest_operation_decisions",
    "review_gate",
]
