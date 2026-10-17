"""Mutation proposal and review API.

A mutation set is a reviewable proposal: an ordered list of typed operations
that reaches canon only through the review gate and the canonical commit
transaction. Reviewers accept, reject, or edit individual operations (an edit is
a new immutable operation revision), then approve or reject the set. Every state
change is recorded with actor, time, and reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_mutation_service
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationSet, MutationStatus
from backend.domain.operations import CanonOperation
from backend.domain.review import OperationDecisionKind
from backend.services.mutation_service import MutationReview, MutationService

router = APIRouter(prefix="/projects/{project_id}/mutations", tags=["canon-mutations"])


class MutationSummaryModel(BaseModel):
    id: str
    status: str
    origin: str
    intent: str
    actor_user_id: str | None = None
    expected_revision: int
    schema_version_id: str | None = None
    created_at: datetime
    updated_at: datetime


class OperationDecisionModel(BaseModel):
    decision: str
    operation_revision: int
    actor_user_id: str | None = None
    note: str | None = None
    created_at: datetime


class MutationOperationModel(BaseModel):
    ordinal: int
    revision: int
    operation_type: str
    payload: dict[str, Any]
    # Latest decision that applies to this operation revision, if any.
    decision: OperationDecisionModel | None = None
    # Current canonical state of the record this operation changes (null for creations).
    target_before: dict[str, Any] | None = None


class ReviewDecisionModel(BaseModel):
    decision: str
    actor_user_id: str | None = None
    note: str | None = None
    created_at: datetime


class MutationTransitionModel(BaseModel):
    from_status: str
    to_status: str
    actor_user_id: str | None = None
    reason: str
    correlation_id: str
    created_at: datetime


class MutationDetailModel(MutationSummaryModel):
    head_revision: int
    is_stale: bool
    requires_review: bool
    review_reasons: list[str]
    operations: list[MutationOperationModel]
    decisions: list[ReviewDecisionModel]
    transitions: list[MutationTransitionModel]
    commit_id: str | None = None
    committed_revision: int | None = None
    # For a stale proposal: ids of the records it touches that changed after it was made.
    changed_since: list[str] = Field(default_factory=list)


class MutationListResponse(BaseModel):
    items: list[MutationSummaryModel]
    limit: int
    offset: int


class ProposeMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[CanonOperation] = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=0)
    origin: str = "author"
    idempotency_key: str = Field(default="", max_length=200)


class ReviseOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: CanonOperation
    reason: str | None = Field(default=None, max_length=500)


class OperationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: OperationDecisionKind
    note: str | None = Field(default=None, max_length=500)


class OperationDecisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=0)
    decision: OperationDecisionKind
    note: str | None = Field(default=None, max_length=500)


class OperationDecisionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[OperationDecisionItem] = Field(min_length=1, max_length=500)


class ReviewNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class RetargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class ValidationIssueModel(BaseModel):
    # Operation ordinal, or -1 for an issue with the proposal as a whole.
    ordinal: int
    code: str
    message: str
    # "blocking" stops a commit; "advisory" only informs the reviewer.
    severity: str


class MutationValidationModel(BaseModel):
    ok: bool
    issues: list[ValidationIssueModel]


class MutationCommitModel(BaseModel):
    commit_id: str
    revision: int
    checksum: str
    status: str


def _summary(mutation: MutationSet) -> dict[str, Any]:
    return {
        "id": mutation.id,
        "status": mutation.status.value,
        "origin": mutation.origin,
        "intent": mutation.intent,
        "actor_user_id": mutation.actor_user_id,
        "expected_revision": mutation.expected_head,
        "schema_version_id": mutation.schema_version_id,
        "created_at": mutation.created_at,
        "updated_at": mutation.updated_at,
    }


def _detail(review: MutationReview) -> MutationDetailModel:
    mutation = review.mutation
    operations = []
    for operation in review.operations:
        decision = review.operation_decisions.get(operation.ordinal)
        operations.append(
            MutationOperationModel(
                ordinal=operation.ordinal,
                revision=operation.revision,
                operation_type=operation.operation_type,
                payload=operation.payload,
                decision=(
                    OperationDecisionModel(
                        decision=decision.decision,
                        operation_revision=decision.operation_revision,
                        actor_user_id=decision.actor_user_id,
                        note=decision.note,
                        created_at=decision.created_at,
                    )
                    if decision is not None
                    else None
                ),
                target_before=review.targets_before.get(operation.ordinal),
            )
        )
    return MutationDetailModel(
        **_summary(mutation),
        head_revision=review.head_revision,
        is_stale=(
            mutation.status is not MutationStatus.COMMITTED
            and review.head_revision != mutation.expected_head
        ),
        requires_review=review.classification.requires_review,
        review_reasons=[reason.value for reason in review.classification.reasons],
        operations=operations,
        decisions=[
            ReviewDecisionModel(
                decision=item.decision,
                actor_user_id=item.actor_user_id,
                note=item.note,
                created_at=item.created_at,
            )
            for item in review.decisions
        ],
        transitions=[
            MutationTransitionModel(
                from_status=item.from_status.value,
                to_status=item.to_status.value,
                actor_user_id=item.actor_user_id,
                reason=item.reason,
                correlation_id=item.correlation_id,
                created_at=item.created_at,
            )
            for item in review.transitions
        ],
        commit_id=review.commit_id,
        committed_revision=review.committed_revision,
        changed_since=list(review.changed_since),
    )


@router.post("", response_model=MutationDetailModel, status_code=201)
async def propose_mutation(
    project_id: str,
    body: ProposeMutationRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    mutation = await service.propose(
        scope,
        workspace_id=scope.workspace_id or "",
        project_id=project_id,
        operations=body.operations,
        expected_head=body.expected_revision,
        origin=body.origin,
        idempotency_key=body.idempotency_key,
    )
    return _detail(await service.review(scope, mutation.id))


@router.get("", response_model=MutationListResponse)
async def list_mutations(
    project_id: str,
    status: MutationStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    scope: AccessScope = Depends(get_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationListResponse:
    mutations = await service.list_mutations(
        scope,
        scope.workspace_id or "",
        project_id,
        status=status.value if status is not None else None,
        limit=limit,
        offset=offset,
    )
    return MutationListResponse(
        items=[MutationSummaryModel(**_summary(item)) for item in mutations],
        limit=limit,
        offset=offset,
    )


@router.get("/{mutation_id}", response_model=MutationDetailModel)
async def get_mutation(
    project_id: str,
    mutation_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    return _detail(await service.review(scope, mutation_id))


@router.post("/{mutation_id}/validate", response_model=MutationValidationModel)
async def validate_mutation(
    project_id: str,
    mutation_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationValidationModel:
    """Advisory preview only; the commit revalidates inside its transaction."""
    report = await service.validate(scope, mutation_id)
    return MutationValidationModel(
        ok=report.ok,
        issues=[
            ValidationIssueModel(
                ordinal=issue.ordinal,
                code=issue.code,
                message=issue.message,
                severity=issue.severity,
            )
            for issue in report.issues
        ],
    )


@router.post("/{mutation_id}/submit", response_model=MutationDetailModel)
async def submit_mutation(
    project_id: str,
    mutation_id: str,
    body: ReviewNoteRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    await service.submit(scope, mutation_id, body.note)
    return _detail(await service.review(scope, mutation_id))


@router.put("/{mutation_id}/operations/{ordinal}", response_model=MutationDetailModel)
async def revise_operation(
    project_id: str,
    mutation_id: str,
    ordinal: int,
    body: ReviseOperationRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    await service.revise_operation(scope, mutation_id, ordinal, body.operation, body.reason)
    return _detail(await service.review(scope, mutation_id))


@router.post("/{mutation_id}/operations/{ordinal}/decision", response_model=MutationDetailModel)
async def decide_operation(
    project_id: str,
    mutation_id: str,
    ordinal: int,
    body: OperationDecisionRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    await service.decide_operation(scope, mutation_id, ordinal, body.decision.value, body.note)
    return _detail(await service.review(scope, mutation_id))


@router.post("/{mutation_id}/operations/decisions", response_model=MutationDetailModel)
async def decide_operations(
    project_id: str,
    mutation_id: str,
    body: OperationDecisionsRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    """Keep or skip several changes in one request: all are recorded, or none."""
    await service.decide_operations(
        scope,
        mutation_id,
        [(item.ordinal, item.decision.value, item.note) for item in body.decisions],
    )
    return _detail(await service.review(scope, mutation_id))


@router.post("/{mutation_id}/approve", response_model=MutationDetailModel)
async def approve_mutation(
    project_id: str,
    mutation_id: str,
    body: ReviewNoteRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    await service.approve(scope, mutation_id, body.note)
    return _detail(await service.review(scope, mutation_id))


@router.post("/{mutation_id}/reject", response_model=MutationDetailModel)
async def reject_mutation(
    project_id: str,
    mutation_id: str,
    body: ReviewNoteRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    await service.reject(scope, mutation_id, body.note)
    return _detail(await service.review(scope, mutation_id))


@router.post("/{mutation_id}/retarget", response_model=MutationDetailModel)
async def retarget_mutation(
    project_id: str,
    mutation_id: str,
    body: RetargetRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationDetailModel:
    """Recover a stale proposal: point it at a newer head and re-review it."""
    await service.retarget(scope, mutation_id, body.expected_revision)
    return _detail(await service.review(scope, mutation_id))


@router.post("/{mutation_id}/commit", response_model=MutationCommitModel)
async def commit_mutation(
    project_id: str,
    mutation_id: str,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: MutationService = Depends(get_mutation_service),
) -> MutationCommitModel:
    result = await service.commit(scope, mutation_id)
    response.headers["ETag"] = f'"{result.revision}"'
    return MutationCommitModel(
        commit_id=result.commit_id,
        revision=result.revision,
        checksum=result.checksum,
        status=result.status,
    )
