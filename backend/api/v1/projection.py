"""Projection status endpoint (ADR-012).

Canonical PostgreSQL is authoritative; the graph projection may lag. This exposes
the durable watermark so a reader can prove freshness or observe lag instead of
trusting a stale graph.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.api.auth import get_project_scope
from backend.api.dependencies import get_archive_service, get_journal, get_projection_service
from backend.api.v1.canon_archive import import_summary
from backend.domain.identity import AccessScope

router = APIRouter(prefix="/projects/{project_id}", tags=["projection"])


@router.get("/projection")
async def projection_lag(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    journal: Any = Depends(get_journal),
    projection: Any = Depends(get_projection_service),
    archive: Any = Depends(get_archive_service),
) -> dict[str, object]:
    branch = await journal.ensure_root_branch(
        workspace_id=scope.workspace_id, project_id=project_id
    )
    lag = await projection.lag(
        workspace_id=scope.workspace_id, project_id=project_id, branch_id=branch.id
    )
    failed = await journal.count_failed_outbox(
        workspace_id=scope.workspace_id, project_id=project_id
    )
    imports = await archive.list_imports_as(scope, scope.workspace_id or "", project_id)
    return {
        # The most recent archive import, if any. Anything other than
        # ``completed`` means this project is not (yet) a successful restore.
        "import": import_summary(imports[0]) if imports else None,
        "projection": "graph",
        "branch_id": branch.id,
        "head_revision": lag.head_revision,
        "applied_revision": lag.applied_revision,
        "lag": lag.lag,
        "status": lag.status.value,
        "current": lag.current,
        "last_error": lag.last_error,
        "failed_deliveries": failed,
    }
