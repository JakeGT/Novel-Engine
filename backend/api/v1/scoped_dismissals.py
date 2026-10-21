"""Dismissals wider than one scene (CON-016): one kind of finding, wherever it is about a
character, or in every scene of a stretch of the story.

``GET`` lists them (withdrawn ones too), each saying in words what it applies to. One is
made from a finding in the studio (``POST /plan/{id}/studio/dismissals`` with
``applies``) or here; ``DELETE`` brings the findings back everywhere it applied, keeping
the record. Only advisory findings are ever dismissed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain.dismissals import MAX_NOTE, DismissalScope, DismissReason, FindingKind
from backend.domain.identity import AccessScope
from backend.services.scoped_dismissals import ScopedDismissalService, ScopedView

router = APIRouter(prefix="/projects/{project_id}", tags=["checks"])


def get_scoped(container: Container = Depends(get_container)) -> ScopedDismissalService:
    return container.scoped_dismissals  # type: ignore[no-any-return]


class ScopedDismissalModel(BaseModel):
    id: str
    kind: Literal["check", "note"]
    code: str
    applies: Literal["character", "range"]
    # What it applies to, in words: "Stock phrasing, in every scene from “A” to “B”".
    applies_to: str
    entity_id: str | None = None
    from_plan_id: str | None = None
    to_plan_id: str | None = None
    reason: DismissReason
    note: str = ""
    expires_at: datetime | None = None
    created_at: datetime
    withdrawn_at: datetime | None = None
    # Still holds: not withdrawn, not run out, and what it names is still there.
    standing: bool
    # Not withdrawn, but it covers nothing now: a scene it names left the plan, or the
    # character left the story. It can still be undone.
    lapsed: bool = False


class ScopedDismissalList(BaseModel):
    items: list[ScopedDismissalModel]


class ScopedDismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["check", "note"]
    code: str = Field(min_length=1, max_length=120)
    applies: Literal["character", "range"]
    entity_id: str | None = None
    from_plan_id: str | None = None
    to_plan_id: str | None = None
    reason: DismissReason
    note: str = Field(default="", max_length=MAX_NOTE)
    days: Literal[7, 30] | None = None


def scoped_model(view: ScopedView) -> ScopedDismissalModel:
    item = view.dismissal
    return ScopedDismissalModel(
        id=item.id,
        kind=item.kind.value,  # type: ignore[arg-type]
        code=item.code,
        applies=item.scope.value,  # type: ignore[arg-type]
        applies_to=view.applies_to,
        entity_id=item.entity_id,
        from_plan_id=item.from_plan_id,
        to_plan_id=item.to_plan_id,
        reason=item.reason,
        note=item.note,
        expires_at=item.expires_at,
        created_at=item.created_at,
        withdrawn_at=item.withdrawn_at,
        standing=view.standing,
        lapsed=view.lapsed,
    )


@router.get("/dismissals/scoped")
async def scoped_dismissals(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: ScopedDismissalService = Depends(get_scoped),
) -> ScopedDismissalList:
    """Every wider dismissal in this line of the story, newest first."""
    found = await service.list(scope, scope.workspace_id or "", project_id)
    return ScopedDismissalList(items=[scoped_model(view) for view in found])


@router.post("/dismissals/scoped", status_code=status.HTTP_201_CREATED)
async def add_scoped_dismissal(
    project_id: str,
    body: ScopedDismissRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: ScopedDismissalService = Depends(get_scoped),
) -> ScopedDismissalModel:
    """Dismiss one kind of finding wherever it is about a character, or in a stretch of
    scenes. Nothing in the story or the checks changes."""
    view = await service.add(
        scope,
        scope.workspace_id or "",
        project_id,
        kind=FindingKind(body.kind),
        code=body.code,
        applies=DismissalScope(body.applies),
        reason=body.reason,
        note=body.note,
        days=body.days,
        entity_id=body.entity_id,
        from_plan_id=body.from_plan_id,
        to_plan_id=body.to_plan_id,
    )
    return scoped_model(view)


@router.delete("/dismissals/scoped/{dismissal_id}")
async def withdraw_scoped_dismissal(
    project_id: str,
    dismissal_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: ScopedDismissalService = Depends(get_scoped),
) -> ScopedDismissalModel:
    """Bring the findings back everywhere it applied. The record stays, withdrawn."""
    return scoped_model(
        await service.withdraw(scope, scope.workspace_id or "", project_id, dismissal_id)
    )
