from datetime import UTC, datetime

import pytest

from backend.domain.journal import MutationOperation, MutationStatus, OperationDecision
from backend.domain.review import (
    BULK_OPERATION_THRESHOLD,
    LEGAL_TRANSITIONS,
    TERMINAL_STATUSES,
    IllegalTransition,
    ReviewReason,
    classify_review,
    effective_operations,
    ensure_transition,
    latest_operation_decisions,
    review_gate,
)

pytestmark = pytest.mark.unit


def _op(ordinal: int, revision: int = 1, op: str = "create_entity") -> MutationOperation:
    return MutationOperation(
        ordinal=ordinal,
        operation_type=op,
        payload={"op": op, "id": f"x{ordinal}"},
        revision=revision,
    )


def _decision(ordinal: int, decision: str, revision: int = 1) -> OperationDecision:
    return OperationDecision(
        id=f"d{ordinal}{decision}{revision}",
        mutation_id="mut_1",
        ordinal=ordinal,
        operation_revision=revision,
        decision=decision,
        created_at=datetime.now(UTC),
    )


def test_every_status_has_a_transition_row_and_terminals_are_closed() -> None:
    assert set(LEGAL_TRANSITIONS) == set(MutationStatus)
    for status in TERMINAL_STATUSES:
        assert LEGAL_TRANSITIONS[status] == frozenset()
    for status, targets in LEGAL_TRANSITIONS.items():
        assert status not in targets


def test_illegal_transition_is_rejected() -> None:
    ensure_transition(MutationStatus.DRAFT, MutationStatus.NEEDS_REVIEW)
    with pytest.raises(IllegalTransition):
        ensure_transition(MutationStatus.COMMITTED, MutationStatus.DRAFT)
    with pytest.raises(IllegalTransition):
        ensure_transition(MutationStatus.REJECTED, MutationStatus.APPROVED)
    with pytest.raises(IllegalTransition):
        ensure_transition(MutationStatus.DRAFT, MutationStatus.COMMITTED)


def test_rejected_operations_are_excluded_and_latest_decision_wins() -> None:
    operations = [_op(0), _op(1), _op(2)]
    decisions = [_decision(1, "rejected"), _decision(2, "rejected"), _decision(2, "accepted")]
    assert [op.ordinal for op in effective_operations(operations, decisions)] == [0, 2]


def test_editing_an_operation_invalidates_its_earlier_decision() -> None:
    operations = [_op(0, revision=2)]
    stale = [_decision(0, "rejected", revision=1)]
    assert latest_operation_decisions(stale, operations) == {}
    assert [op.ordinal for op in effective_operations(operations, stale)] == [0]


@pytest.mark.parametrize(
    ("origin", "intent", "types", "expected"),
    [
        ("author", "edit", ["create_entity", "assert_fact"], set()),
        ("model", "edit", ["create_entity"], {ReviewReason.PROTECTED_ORIGIN}),
        ("import", "edit", ["create_entity"], {ReviewReason.PROTECTED_ORIGIN}),
        ("author", "edit", ["retire_entity"], {ReviewReason.DESTRUCTIVE_OPERATION}),
        ("author", "edit", ["retire_event"], {ReviewReason.DESTRUCTIVE_OPERATION}),
        ("author", "revert", ["close_fact"], {ReviewReason.CONFLICT_RESOLUTION}),
        (
            "author",
            "edit",
            ["create_entity"] * (BULK_OPERATION_THRESHOLD + 1),
            {ReviewReason.BULK_CHANGE},
        ),
        ("migration", "revert", ["retire_entity"], set()),
    ],
)
def test_review_classification(origin, intent, types, expected) -> None:
    result = classify_review(origin=origin, intent=intent, operation_types=types)
    assert set(result.reasons) == expected
    assert result.requires_review is bool(expected)


def test_gate_allows_low_impact_author_draft_and_blocks_everything_unapproved() -> None:
    assert review_gate(
        origin="author",
        intent="edit",
        status=MutationStatus.DRAFT,
        operation_types=["create_entity"],
    ).ok
    for origin, types in (("model", ["create_entity"]), ("author", ["retire_entity"])):
        blocked = review_gate(
            origin=origin, intent="edit", status=MutationStatus.DRAFT, operation_types=types
        )
        assert blocked.code == "review_required"
        assert review_gate(
            origin=origin, intent="edit", status=MutationStatus.APPROVED, operation_types=types
        ).ok


def test_gate_blocks_rejected_empty_and_pending_sets() -> None:
    assert (
        review_gate(
            origin="author",
            intent="edit",
            status=MutationStatus.REJECTED,
            operation_types=["create_entity"],
        ).code
        == "review_rejected"
    )
    assert (
        review_gate(
            origin="author", intent="edit", status=MutationStatus.APPROVED, operation_types=[]
        ).code
        == "empty_mutation"
    )
    # A set explicitly sent to review may not be committed until it is decided.
    assert (
        review_gate(
            origin="author",
            intent="edit",
            status=MutationStatus.NEEDS_REVIEW,
            operation_types=["create_entity"],
        ).code
        == "review_required"
    )
