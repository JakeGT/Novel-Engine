"""Media identity and explicit evidence-link HTTP routes (2I)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_media_service
from backend.domain.identity import AccessScope
from backend.services.media_service import MediaService

router = APIRouter(prefix="/projects/{project_id}", tags=["media"])


def _asset(asset: Any) -> dict[str, Any]:
    return {
        "id": asset.id,
        "name": asset.name,
        "lifecycle": asset.lifecycle.value,
        "created_revision": asset.created_revision,
        "retired_revision": asset.retired_revision,
        "created_at": asset.created_at,
    }


def _version(version: Any) -> dict[str, Any]:
    return {
        "id": version.id,
        "asset_id": version.asset_id,
        "version": version.version,
        "content_type": version.content_type,
        "byte_length": version.byte_length,
        "checksum": version.checksum,
        "object_key": version.object_key,
        "source": version.source,
        "licence": version.licence,
        "attribution": version.attribution,
        "authority": version.authority,
        "visibility": version.visibility,
        "created_revision": version.created_revision,
        "retired_revision": version.retired_revision,
        "created_at": version.created_at,
    }


def _link(link: Any) -> dict[str, Any]:
    return {
        "id": link.id,
        "asset_id": link.asset_id,
        "asset_version_id": link.asset_version_id,
        "role": link.role.value,
        "target_record_type": link.target_record_type,
        "target_record_id": link.target_record_id,
        "created_revision": link.created_revision,
        "retired_revision": link.retired_revision,
        "created_at": link.created_at,
    }


@router.get("/assets")
async def list_assets(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    assets = await media.list_assets(scope, scope.workspace_id or "", project_id)
    return {"items": [_asset(a) for a in assets]}


@router.post("/assets", status_code=201)
async def register_asset(
    project_id: str,
    payload: dict[str, Any],
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    asset = await media.register_asset(
        scope, scope.workspace_id or "", project_id, name=str(payload.get("name", ""))
    )
    return _asset(asset)


@router.get("/assets/{asset_id}")
async def get_asset(
    project_id: str,
    asset_id: str,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    asset = await media.get_asset(scope, scope.workspace_id or "", project_id, asset_id)
    return _asset(asset)


@router.get("/assets/{asset_id}/lifecycle")
async def asset_lifecycle(
    project_id: str,
    asset_id: str,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    """Append-only audit of registration, import, and retirement for this asset."""
    events = await media.list_lifecycle(scope, scope.workspace_id or "", project_id, asset_id)
    return {
        "items": [
            {
                "action": event.action.value,
                "asset_id": event.asset_id,
                "asset_version_id": event.asset_version_id,
                "actor_user_id": event.actor_user_id,
                "canon_revision": event.canon_revision,
                "created_at": event.created_at,
            }
            for event in events
        ]
    }


@router.get("/assets/{asset_id}/versions")
async def list_versions(
    project_id: str,
    asset_id: str,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    versions = await media.list_versions(scope, scope.workspace_id or "", project_id, asset_id)
    return {"items": [_version(v) for v in versions]}


@router.post("/assets/{asset_id}/versions", status_code=201)
async def register_version(
    project_id: str,
    asset_id: str,
    payload: dict[str, Any],
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    version = await media.register_version(
        scope,
        scope.workspace_id or "",
        project_id,
        asset_id,
        object_key=str(payload.get("object_key", "")),
        source=payload.get("source"),
        licence=payload.get("licence"),
        attribution=payload.get("attribution"),
        authority=payload.get("authority"),
        visibility=payload.get("visibility"),
    )
    return _version(version)


@router.post("/assets/{asset_id}/retire")
async def retire_asset(
    project_id: str,
    asset_id: str,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    asset = await media.retire_asset(scope, scope.workspace_id or "", project_id, asset_id)
    return _asset(asset)


@router.post("/assets/{asset_id}/versions/{version_id}/retire")
async def retire_version(
    project_id: str,
    asset_id: str,
    version_id: str,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    version = await media.retire_version(scope, scope.workspace_id or "", project_id, version_id)
    return _version(version)


@router.get("/assets/{asset_id}/links")
async def list_links(
    project_id: str,
    asset_id: str,
    include_retired: bool = False,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    links = await media.list_links(
        scope,
        scope.workspace_id or "",
        project_id,
        asset_id=asset_id,
        include_retired=include_retired,
    )
    return {"items": [_link(link) for link in links]}


@router.post("/assets/{asset_id}/links", status_code=201)
async def create_link(
    project_id: str,
    asset_id: str,
    payload: dict[str, Any],
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    result, link = await media.create_link(
        scope,
        scope.workspace_id or "",
        project_id,
        asset_id=asset_id,
        target_id=str(payload.get("target_id", "")),
        role=str(payload.get("role", "EVIDENCE_FOR")),
        asset_version_id=payload.get("asset_version_id"),
        target_record_type=str(payload.get("target_record_type", "entity")),
        expected_revision=int(payload.get("expected_revision", 0)),
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _link(link)


@router.get("/links")
async def list_project_links(
    project_id: str,
    target_id: str | None = None,
    include_retired: bool = False,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    """Evidence links in the project, optionally only those attached to one record."""
    links = await media.list_links(
        scope,
        scope.workspace_id or "",
        project_id,
        target_id=target_id,
        include_retired=include_retired,
    )
    return {"items": [_link(link) for link in links]}


@router.post("/links/{link_id}/retire")
async def retire_link(
    project_id: str,
    link_id: str,
    payload: dict[str, Any],
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    result = await media.retire_link(
        scope,
        scope.workspace_id or "",
        project_id,
        link_id,
        expected_revision=int(payload.get("expected_revision", 0)),
    )
    return {"link_id": link_id, "revision": result.revision}
