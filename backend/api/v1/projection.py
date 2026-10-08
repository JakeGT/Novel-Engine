"""Projection status endpoint (ADR-012).

Canonical PostgreSQL is authoritative; the graph projection may lag. This exposes
the durable watermark so a reader can prove freshness or observe lag instead of
trusting a stale graph.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.api.auth import get_project_scope
from backend.api.dependencies import get_journal, get_projection_service
from backend.domain.identity import AccessScope

router = APIRouter(prefix="/projects/{project_id}", tags=["projection"])


@router.get("/projection")
async def projection_lag(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    journal: Any = Depends(get_journal),
    projection: Any = Depends(get_projection_service),
) -> dict[str, object]:
    branch = await journal.ensure_root_branch(
        workspace_id=scope.workspace_id, project_id=project_id
    )
    lag = await projection.lag(
        workspace_id=scope.workspace_id, project_id=project_id, branch_id=branch.id
    )
    return {
        "projection": "graph",
        "branch_id": branch.id,
        "head_revision": lag.head_revision,
        "applied_revision": lag.applied_revision,
        "lag": lag.lag,
        "status": lag.status.value,
        "current": lag.current,
    }
