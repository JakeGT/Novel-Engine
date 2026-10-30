"""Project archive export and import-status routes.

Export returns the complete accepted history of a project and is restricted to
project administrators (workspace owners); every export writes a security-audit
record. Archive *import* is deliberately not an HTTP route: restoring a project
is an operator action (``make archive``) until the secure ingestion pipeline
exists. Its status ledger is readable here so the UI never presents a partial
restore as a successful project.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_archive_service
from backend.domain.archive import ArchiveImport
from backend.domain.identity import AccessScope
from backend.services.canon_archive import CanonArchiveService

router = APIRouter(prefix="/projects/{project_id}", tags=["archive"])


def import_summary(record: ArchiveImport) -> dict[str, Any]:
    return {
        "id": record.id,
        "status": record.status.value,
        "stage": record.stage,
        "archive_checksum": record.archive_checksum,
        "format_version": record.format_version,
        "commits_total": record.commits_total,
        "commits_applied": record.commits_applied,
        "objects_imported": record.objects_imported,
        "error": record.error,
        "started_at": record.started_at,
        "updated_at": record.updated_at,
        "completed_at": record.completed_at,
    }


@router.post("/archive/export")
async def export_archive(
    project_id: str,
    scope: AccessScope = Depends(require_project_scope),
    archive: CanonArchiveService = Depends(get_archive_service),
) -> dict[str, Any]:
    """Export the project's full canonical history (owner only, audited)."""
    return await archive.export_project_as(scope, scope.workspace_id or "", project_id)


@router.get("/imports")
async def list_imports(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    archive: CanonArchiveService = Depends(get_archive_service),
) -> dict[str, Any]:
    records = await archive.list_imports_as(scope, scope.workspace_id or "", project_id)
    return {"items": [import_summary(record) for record in records]}


@router.delete("/imports/{import_id}")
async def remove_import(
    project_id: str,
    import_id: str,
    scope: AccessScope = Depends(require_project_scope),
    archive: CanonArchiveService = Depends(get_archive_service),
) -> dict[str, Any]:
    """Erase the canon written by a failed or interrupted import (owner only)."""
    record = await archive.remove_import_as(scope, scope.workspace_id or "", project_id, import_id)
    return import_summary(record)
