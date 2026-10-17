"""What-ifs: try a turn of the story without touching it (ADR-020).

Work inside a what-if by sending its id in the ``X-Story-Branch`` header on any
project request; with no header everything is the main story.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain.identity import AccessScope
from backend.domain.journal import Branch
from backend.services.what_if_service import Keep, RecordChange, WhatIfService

router = APIRouter(prefix="/projects/{project_id}", tags=["what-ifs"])


def get_what_if_service(container: Container = Depends(get_container)) -> WhatIfService:
    return container.what_if_service  # type: ignore[no-any-return]


class WhatIfModel(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str
    started_at_version: int
    version: int
    changes: int
    created_at: datetime


class StartWhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class SetAsideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aside: bool = True


class ChangeModel(BaseModel):
    key: str
    record_type: str
    record_id: str
    kind: str
    label: str


class ComparisonModel(BaseModel):
    what_if: WhatIfModel
    main_version: int
    in_what_if: list[ChangeModel]
    in_main: list[ChangeModel]
    conflicts: list[ChangeModel]


class BringBackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # For each record changed in both lines, whose version to keep.
    keep: dict[str, Keep] = Field(default_factory=dict)


class BringBackResult(BaseModel):
    mutation_id: str


def _model(branch: Branch, changes: int | None = None) -> WhatIfModel:
    started = branch.fork_revision or 0
    return WhatIfModel(
        id=branch.id,
        name=branch.name,
        description=branch.description,
        status=branch.status,
        started_at_version=started,
        version=branch.head_revision,
        changes=changes if changes is not None else branch.head_revision - started,
        created_at=branch.created_at,
    )


def _change(change: RecordChange) -> ChangeModel:
    return ChangeModel(
        key=change.key,
        record_type=change.record_type,
        record_id=change.record_id,
        kind=change.kind.value,
        label=change.label,
    )


@router.get("/what-ifs")
async def list_what_ifs(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: WhatIfService = Depends(get_what_if_service),
) -> dict:
    items = await service.what_ifs(scope, scope.workspace_id or "", project_id)
    return {"items": [_model(item.branch, item.changes) for item in items]}


@router.post("/what-ifs", status_code=status.HTTP_201_CREATED)
async def start_what_if(
    project_id: str,
    body: StartWhatIfRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: WhatIfService = Depends(get_what_if_service),
) -> WhatIfModel:
    branch = await service.start(
        scope, scope.workspace_id or "", project_id, name=body.name, description=body.description
    )
    return _model(branch)


@router.get("/what-ifs/{branch_id}")
async def get_what_if(
    project_id: str,
    branch_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: WhatIfService = Depends(get_what_if_service),
) -> WhatIfModel:
    return _model(await service.get(scope, scope.workspace_id or "", project_id, branch_id))


@router.post("/what-ifs/{branch_id}/set-aside")
async def set_aside_what_if(
    project_id: str,
    branch_id: str,
    body: SetAsideRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: WhatIfService = Depends(get_what_if_service),
) -> WhatIfModel:
    branch = await service.set_aside(
        scope, scope.workspace_id or "", project_id, branch_id, body.aside
    )
    return _model(branch)


@router.get("/what-ifs/{branch_id}/compare")
async def compare_what_if(
    project_id: str,
    branch_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: WhatIfService = Depends(get_what_if_service),
) -> ComparisonModel:
    comparison = await service.compare(scope, scope.workspace_id or "", project_id, branch_id)
    return ComparisonModel(
        what_if=_model(comparison.branch),
        main_version=comparison.main_head,
        in_what_if=[_change(change) for change in comparison.in_what_if],
        in_main=[_change(change) for change in comparison.in_main],
        conflicts=[_change(change) for change in comparison.conflicts],
    )


@router.post("/what-ifs/{branch_id}/bring-back", status_code=status.HTTP_201_CREATED)
async def bring_back_what_if(
    project_id: str,
    branch_id: str,
    body: BringBackRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: WhatIfService = Depends(get_what_if_service),
) -> BringBackResult:
    mutation = await service.bring_back(
        scope, scope.workspace_id or "", project_id, branch_id, keep=body.keep
    )
    return BringBackResult(mutation_id=mutation.id)
