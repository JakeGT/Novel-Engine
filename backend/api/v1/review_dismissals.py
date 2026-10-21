"""Advisory findings on a proposal in review, and dismissing them (CON-016).

``GET`` lists the proposal's advisory findings as they stand, each with the key that
names it and the author's dismissal if it still holds. A dismissal is for this proposal
only and comes back by itself when the change is edited, the rule it cites changes, or
the story makes the finding say something else. A finding that blocks is never listed
here and can never be dismissed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain.dismissals import MAX_NOTE, DismissReason, ReviewDismissal
from backend.domain.identity import AccessScope
from backend.services.review_dismissals import ReviewDismissalService, ReviewFinding

router = APIRouter(prefix="/projects/{project_id}/mutations/{mutation_id}", tags=["review"])


def get_review_dismissals(container: Container = Depends(get_container)) -> ReviewDismissalService:
    return container.review_dismissals  # type: ignore[no-any-return]


class ReviewDismissalModel(BaseModel):
    id: str
    reason: DismissReason
    note: str = ""
    created_at: datetime


class ReviewFindingModel(BaseModel):
    # The change it is about (its ordinal), or -1 for the proposal as a whole.
    ordinal: int
    code: str
    message: str
    severity: Literal["advisory"] = "advisory"
    # Names this finding when dismissing it.
    key: str
    dismissal: ReviewDismissalModel | None = None
    # Dismissed before, and back because the change or what the finding says changed.
    came_back: bool = False


class ReviewFindingList(BaseModel):
    items: list[ReviewFindingModel]


class ReviewDismissRequest(BaseModel):
    key: str = Field(min_length=64, max_length=64)
    reason: DismissReason
    note: str = Field(default="", max_length=MAX_NOTE)


def _dismissal(found: ReviewDismissal | None) -> ReviewDismissalModel | None:
    if found is None:
        return None
    return ReviewDismissalModel(
        id=found.id, reason=found.reason, note=found.note, created_at=found.created_at
    )


def _list(found: list[ReviewFinding]) -> ReviewFindingList:
    return ReviewFindingList(
        items=[
            ReviewFindingModel(
                ordinal=item.ordinal,
                code=item.code,
                message=item.message,
                key=item.key,
                dismissal=_dismissal(item.dismissal),
                came_back=item.came_back,
            )
            for item in found
        ]
    )


@router.get("/findings")
async def review_findings(
    project_id: str,
    mutation_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: ReviewDismissalService = Depends(get_review_dismissals),
) -> ReviewFindingList:
    """The advisory findings on these changes now, and which the author dismissed."""
    return _list(await service.findings(scope, mutation_id))


@router.post("/findings/dismissals")
async def dismiss_review_finding(
    project_id: str,
    mutation_id: str,
    body: ReviewDismissRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: ReviewDismissalService = Depends(get_review_dismissals),
) -> ReviewFindingList:
    """Dismiss an advisory finding on these changes, with a reason. Nothing else changes."""
    return _list(
        await service.dismiss(scope, mutation_id, key=body.key, reason=body.reason, note=body.note)
    )


@router.delete("/findings/dismissals/{dismissal_id}")
async def undo_review_dismissal(
    project_id: str,
    mutation_id: str,
    dismissal_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: ReviewDismissalService = Depends(get_review_dismissals),
) -> ReviewFindingList:
    """Bring a dismissed finding back. The dismissal stays on record, withdrawn."""
    return _list(await service.undo(scope, mutation_id, dismissal_id))
