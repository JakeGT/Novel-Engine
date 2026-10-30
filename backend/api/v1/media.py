"""Media identity and explicit evidence-link HTTP routes (2I)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_media_service
from backend.domain.identity import AccessScope
from backend.domain.media import (
    PROCESSING_FAILURES,
    MediaSegment,
    PictureUpload,
    UploadTarget,
    VariantKind,
)
from backend.services.media_service import (
    PROCESSOR_VERSION,
    LibraryPicture,
    MediaService,
    ProcessingView,
    ReceivedUpload,
    receive_picture,
)

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
        # When its file was deleted (AST-008); the record stays as the tombstone.
        "deleted_at": getattr(version, "deleted_at", None),
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
                **({"item_count": event.item_count} if event.item_count is not None else {}),
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


@router.post("/entities/{entity_id}/pictures", status_code=201)
async def add_entity_picture(
    project_id: str,
    entity_id: str,
    request: Request,
    response: Response,
    expected_revision: int = Query(ge=0),
    name: str | None = Query(default=None, max_length=200),
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    """Add a picture to an entry. The request body is the image itself (PNG, JPEG, or WebP).

    The image is stored as a new media asset and linked to the entry as a picture
    of it, in one new revision. Its type is taken from its bytes, not the request.
    """
    received = await _upload(request)
    result, link, version = await media.add_picture(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_id=entity_id,
        data=received.data,
        name=name,
        expected_revision=expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return {"link": _link(link), "version": _version(version), "received": _received(received)}


async def _upload(request: Request) -> ReceivedUpload:
    """The request body, read as it streams in (AST-002): refused before reading when its
    declared length is too large, as soon as too much has arrived, or once its first
    bytes show it is not a picture; hashed on the way, and checked against a
    ``Content-Digest`` (or ``Repr-Digest``) the sender gave."""
    return await receive_picture(
        request.stream(),
        declared_length=request.headers.get("content-length"),
        content_digest=request.headers.get("content-digest") or request.headers.get("repr-digest"),
    )


def _received(received: ReceivedUpload) -> dict[str, Any]:
    """What arrived, before hidden details were taken out: so the sender can check it.
    ``digest`` says whether the sender's digest was checked (only sha-256 is)."""
    answer: dict[str, Any] = {
        "bytes": received.size,
        "sha256": received.sha256,
        "digest": received.digest,
    }
    if received.digest == "not_checked":
        answer["note"] = (
            "The digest sent was not checked: only a sha-256 digest is checked here. "
            "Compare the sha-256 above instead."
        )
    return answer


UploadFor = Literal["entity", "scene", "picture"]
_UPLOAD_TARGETS = {
    "entity": UploadTarget.ENTITY,
    "scene": UploadTarget.SCENE,
    "picture": UploadTarget.ASSET,
}
_UPLOAD_FOR = {target: name for name, target in _UPLOAD_TARGETS.items()}


class UploadStartRequest(BaseModel):
    """Ask to send a picture in two steps (AST-002): what it is for and what will come."""

    model_config = ConfigDict(extra="forbid")
    # entity: a picture of an entry; scene: shown on a scene of the plan; picture: a new
    # version of a picture already in the story.
    upload_for: UploadFor = Field(alias="for")
    id: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    byte_length: int = Field(gt=0)
    # The picture's SHA-256, as 64 hexadecimal letters and digits.
    sha256: str = Field(min_length=64, max_length=64)


class UploadModel(BaseModel):
    id: str
    upload_for: UploadFor = Field(serialization_alias="for")
    target_id: str
    # waiting (send the picture), received (finish it), finishing, finished or expired.
    state: Literal["waiting", "received", "finishing", "finished", "expired"]
    byte_length: int
    sha256: str
    expires_at: datetime
    # Where to send the bytes (PUT) and where to finish (POST), relative to the API.
    send_to: str
    finish_at: str
    asset_id: str | None = None
    version_id: str | None = None
    link_id: str | None = None

    model_config = ConfigDict(populate_by_name=True)


def _upload_model(upload: PictureUpload, project_id: str) -> UploadModel:
    base = f"/api/v1/projects/{project_id}/uploads/{upload.id}"
    return UploadModel(
        id=upload.id,
        upload_for=_UPLOAD_FOR[upload.target_type],  # type: ignore[arg-type]
        target_id=upload.target_id,
        state=upload.state.value,
        byte_length=upload.expected_bytes,
        sha256=upload.expected_sha256,
        expires_at=upload.expires_at,
        send_to=base,
        finish_at=f"{base}/finish",
        asset_id=upload.asset_id,
        version_id=upload.version_id,
        link_id=upload.link_id,
    )


@router.post("/uploads", status_code=201, response_model_by_alias=True)
async def start_upload(
    project_id: str,
    body: UploadStartRequest,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> UploadModel:
    """Start a two-step picture upload (AST-002): say what it is for, its size and its
    SHA-256. Refused before anything is sent when it is too large (413), there is no
    room, or what it is for is not in the story. Then send the bytes with ``PUT`` to
    ``send_to`` and finish with ``POST`` to ``finish_at``, within an hour."""
    upload = await media.start_upload(
        scope,
        scope.workspace_id or "",
        project_id,
        target_type=_UPLOAD_TARGETS[body.upload_for],
        target_id=body.id,
        byte_length=body.byte_length,
        sha256=body.sha256,
        name=body.name,
    )
    return _upload_model(upload, project_id)


@router.get("/uploads/{upload_id}", response_model_by_alias=True)
async def get_upload(
    project_id: str,
    upload_id: str,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> UploadModel:
    """Where a two-step upload is. Only the person who started it can see it."""
    upload = await media._own_upload(  # noqa: SLF001 - the same check as every step
        scope, scope.workspace_id or "", project_id, upload_id
    )
    return _upload_model(upload, project_id)


class UploadReceivedModel(BaseModel):
    upload: UploadModel
    # What arrived, as for a one-step upload.
    received: dict[str, Any]


@router.put("/uploads/{upload_id}")
async def send_upload(
    project_id: str,
    upload_id: str,
    request: Request,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> UploadReceivedModel:
    """Send the picture itself as the request body. It is read as it streams in, never
    past the size promised, and must match the size and SHA-256 promised; a
    ``Content-Digest`` is checked too. When it does not match nothing is kept and it can
    be sent again. It is not in the story until it is finished."""
    upload, received = await media.receive_upload(
        scope,
        scope.workspace_id or "",
        project_id,
        upload_id,
        request.stream(),
        declared_length=request.headers.get("content-length"),
        content_digest=request.headers.get("content-digest") or request.headers.get("repr-digest"),
    )
    return UploadReceivedModel(
        upload=_upload_model(upload, project_id), received=_received(received)
    )


class UploadFinishedModel(BaseModel):
    upload: UploadModel
    # The new picture's version, and where it is shown (null for a new version of a
    # picture already in the story).
    version: dict[str, Any]
    link: dict[str, Any] | None = None


@router.post("/uploads/{upload_id}/finish")
async def finish_upload(
    project_id: str,
    upload_id: str,
    response: Response,
    expected_revision: int | None = Query(default=None, ge=0),
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> UploadFinishedModel:
    """Finish a two-step upload: the stored bytes are checked against what was promised
    again, then the picture is shown on its entry or scene (``expected_revision`` is the
    story's revision) or added as a new version of its picture. Asking again once it is
    finished answers with what it made."""
    upload, link, version = await media.finish_upload(
        scope,
        scope.workspace_id or "",
        project_id,
        upload_id,
        expected_revision=expected_revision,
    )
    return UploadFinishedModel(
        upload=_upload_model(upload, project_id),
        version=_version(version),
        link=_link(link) if link is not None else None,
    )


@router.post("/plan/{plan_id}/pictures", status_code=201)
async def add_scene_picture(
    project_id: str,
    plan_id: str,
    request: Request,
    response: Response,
    expected_revision: int = Query(ge=0),
    name: str | None = Query(default=None, max_length=200),
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> dict[str, Any]:
    """Add a picture to a scene of the plan. The request body is the image itself.

    The same checks as a picture of an entry; the link points at the scene
    (``target_record_type`` ``plan_item``, role ``DEPICTS``), in one new revision.
    """
    received = await _upload(request)
    result, link, version = await media.add_scene_picture(
        scope,
        scope.workspace_id or "",
        project_id,
        scene_id=plan_id,
        data=received.data,
        name=name,
        expected_revision=expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return {"link": _link(link), "version": _version(version), "received": _received(received)}


ProcessingState = Literal["queued", "processing", "ready", "failed"]


class ProcessingModel(BaseModel):
    """How far a picture is in being prepared (its smaller copies), in plain words."""

    # None: never queued (processing is off, or the picture is older than it).
    status: ProcessingState | None
    failure_code: str | None = None
    # What the author is told when it failed; never a decoder's own words.
    message: str | None = None
    # Whether Retry is offered; ``helps`` says whether trying again unchanged may work.
    retryable: bool
    helps: bool = False
    attempts: int = 0
    width: int | None = None
    height: int | None = None
    copies: list[str] = Field(default_factory=list)
    # Prepared by the processor this server runs now, from the stored bytes.
    current: bool = False


def _processing(view: ProcessingView) -> ProcessingModel:
    helps = bool(view.failure_code and PROCESSING_FAILURES.get(view.failure_code, ("", True))[1])
    return ProcessingModel(
        status=view.status.value if view.status is not None else None,
        failure_code=view.failure_code,
        message=view.message,
        retryable=view.retryable,
        helps=helps,
        attempts=view.attempts,
        width=view.width,
        height=view.height,
        copies=list(view.copies),
        current=view.current,
    )


class LibraryPictureModel(BaseModel):
    asset_id: str
    name: str
    version_id: str
    content_type: str
    byte_length: int
    created_at: datetime
    # How many entries and scenes show it now; 0 when it is shown nowhere.
    shown: int
    # Where its preparation is; None before it was ever queued.
    processing: ProcessingState | None = None
    processing_message: str | None = None
    # Whether a thumbnail exists to show in lists (else the original is shown).
    has_thumbnail: bool = False


class StorageModel(BaseModel):
    used_bytes: int
    # 0 means no limit.
    quota_bytes: int


class PictureLibraryModel(BaseModel):
    items: list[LibraryPictureModel]
    storage: StorageModel | None = None


def _library_picture(picture: LibraryPicture) -> LibraryPictureModel:
    state = picture.processing
    failed = state is not None and state.status.value == "failed"
    return LibraryPictureModel(
        asset_id=picture.asset.id,
        name=picture.asset.name,
        version_id=picture.version.id,
        content_type=picture.version.content_type,
        byte_length=picture.version.byte_length,
        created_at=picture.asset.created_at,
        shown=picture.shown,
        processing=state.status.value if state is not None else None,
        processing_message=(
            PROCESSING_FAILURES.get(
                state.failure_code or "", PROCESSING_FAILURES["processor_failed"]
            )[0]
            if failed and state is not None
            else None
        ),
        has_thumbnail=picture.has_thumbnail,
    )


@router.get("/pictures")
async def picture_library(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> PictureLibraryModel:
    """Every picture in the story that can be shown, newest first, including those
    shown nowhere now. Choosing one links it again; nothing is copied. Each says how far
    its preparation is, and the workspace's room for pictures is given once."""
    workspace_id = scope.workspace_id or ""
    pictures = await media.picture_library(scope, workspace_id, project_id)
    room = await media.storage(scope, workspace_id, project_id)
    return PictureLibraryModel(
        items=[_library_picture(picture) for picture in pictures],
        storage=StorageModel(**room),
    )


class FoundPictureModel(LibraryPictureModel):
    # "words": its name or reviewed description holds every word; "meaning": found by
    # search by meaning.
    reason: Literal["words", "meaning"]
    # "name", "description", "picture", "caption" or "part" (a part the author marked).
    matched: str
    score: float | None = None
    # The marked part's name (or the entry it shows) when a part matched.
    part: str | None = None


class PictureSearchModel(BaseModel):
    items: list[FoundPictureModel]
    by_meaning: bool
    # Why search by meaning was not used, in plain words; None when it was.
    note: str | None = None


@router.get("/pictures/search")
async def search_pictures(
    project_id: str,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=24, ge=1, le=60),
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> PictureSearchModel:
    """Pictures of this line of the story for some words: by their name or reviewed
    description first, then by meaning when that is set up (AST-007)."""
    found = await media.search_pictures(scope, scope.workspace_id or "", project_id, q, limit)
    return PictureSearchModel(
        items=[
            FoundPictureModel(
                **_library_picture(item.picture).model_dump(),
                reason=item.reason,  # type: ignore[arg-type]
                matched=item.matched,
                score=item.score,
                part=item.part,
            )
            for item in found.found
        ],
        by_meaning=found.by_meaning,
        note=found.note,
    )


class DeletedPictureModel(BaseModel):
    asset_id: str
    lifecycle: str
    versions: int
    files_removed: int
    copies_removed: int
    bytes_freed: int
    # What a model said about it (captions and details), removed with it (AST-008).
    notes_removed: int = 0


@router.post("/assets/{asset_id}/delete")
async def delete_picture(
    project_id: str,
    asset_id: str,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> DeletedPictureModel:
    """Delete a picture's files before its story is deleted (AST-008). Owners and
    administrators only, and only once it is shown nowhere. The record stays as a
    tombstone; the files, their smaller copies and what search had of them are gone."""
    deleted = await media.delete_picture(scope, scope.workspace_id or "", project_id, asset_id)
    return DeletedPictureModel(
        asset_id=deleted.asset.id,
        lifecycle=deleted.asset.lifecycle.value,
        versions=deleted.versions,
        files_removed=deleted.files_removed,
        copies_removed=deleted.copies_removed,
        bytes_freed=deleted.bytes_freed,
        notes_removed=deleted.notes_removed,
    )


PictureSize = Literal["thumbnail", "web", "original"]


@router.get("/assets/{asset_id}/versions/{version_id}/content")
async def get_version_content(
    project_id: str,
    asset_id: str,
    version_id: str,
    request: Request,
    size: PictureSize = Query(default="original"),
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> Response:
    """The image bytes of one media version, for members of the project.

    ``size`` asks for a smaller copy (``thumbnail`` 256px, ``web`` 1024px, WebP with no
    metadata); when that copy is not made yet the original is sent. The
    ``X-Picture-Size`` header says which was sent."""
    copy = None if size == "original" else VariantKind(size)
    data, content_type, served = await media.open_picture(
        scope, scope.workspace_id or "", project_id, asset_id, version_id, copy=copy
    )
    headers = {
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        # If opened directly, the file can do nothing but be displayed.
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Picture-Size": served,
    }
    if served != size:
        # A fallback must not hide the copy once it is made.
        headers["Cache-Control"] = "private, no-cache"
    elif served == "original":
        # A version's bytes never change.
        headers["Cache-Control"] = "private, max-age=31536000, immutable"
    else:
        # A copy is made again when the processor changes, at the same address: it is
        # kept an hour, then checked against a tag naming the processor and the bytes.
        digest = hashlib.sha256(data).hexdigest()[:32]
        etag = f'"{served}-{PROCESSOR_VERSION}-{digest}"'
        headers["Cache-Control"] = "private, max-age=3600"
        headers["ETag"] = etag
        if etag in {tag.strip() for tag in request.headers.get("if-none-match", "").split(",")}:
            return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=content_type, headers=headers)


@router.get("/assets/{asset_id}/versions/{version_id}/processing")
async def get_processing(
    project_id: str,
    asset_id: str,
    version_id: str,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> ProcessingModel:
    """How far this picture is in being prepared, and why it failed if it did."""
    view = await media.processing_status(
        scope, scope.workspace_id or "", project_id, asset_id, version_id
    )
    return _processing(view)


@router.post("/assets/{asset_id}/versions/{version_id}/processing/retry", status_code=202)
async def retry_processing(
    project_id: str,
    asset_id: str,
    version_id: str,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> ProcessingModel:
    """Prepare this picture again. A picture already prepared from these bytes by the
    processor this server runs is left as it is; asking twice queues it once."""
    view = await media.retry_processing(
        scope, scope.workspace_id or "", project_id, asset_id, version_id
    )
    return _processing(view)


class SegmentModel(BaseModel):
    id: str
    kind: Literal["whole", "region"]
    x: int
    y: int
    width: int
    height: int
    label: str | None = None
    # The entry this part shows, when the author said (AST-007).
    entity_id: str | None = None
    created_at: datetime


class SegmentListModel(BaseModel):
    items: list[SegmentModel]


class RegionRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=200)
    entity_id: str | None = Field(default=None, max_length=200)


def _segment(segment: MediaSegment) -> SegmentModel:
    return SegmentModel(
        id=segment.id,
        kind=segment.kind.value,
        x=segment.x,
        y=segment.y,
        width=segment.width,
        height=segment.height,
        label=segment.label,
        entity_id=segment.entity_id,
        created_at=segment.created_at,
    )


@router.get("/assets/{asset_id}/versions/{version_id}/segments")
async def list_segments(
    project_id: str,
    asset_id: str,
    version_id: str,
    scope: AccessScope = Depends(get_project_scope),
    media: MediaService = Depends(get_media_service),
) -> SegmentListModel:
    """The whole picture (once prepared) and the parts of it the author marked."""
    segments = await media.list_segments(
        scope, scope.workspace_id or "", project_id, asset_id, version_id
    )
    return SegmentListModel(items=[_segment(segment) for segment in segments])


@router.post("/assets/{asset_id}/versions/{version_id}/segments", status_code=201)
async def add_region(
    project_id: str,
    asset_id: str,
    version_id: str,
    payload: RegionRequest,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> SegmentModel:
    """Mark a rectangle of a prepared picture, in pixels of the picture as shown."""
    segment = await media.add_region(
        scope,
        scope.workspace_id or "",
        project_id,
        asset_id,
        version_id,
        x=payload.x,
        y=payload.y,
        width=payload.width,
        height=payload.height,
        label=payload.label,
        entity_id=payload.entity_id,
    )
    return _segment(segment)


@router.put("/assets/{asset_id}/versions/{version_id}/segments/{segment_id}")
async def update_region(
    project_id: str,
    asset_id: str,
    version_id: str,
    segment_id: str,
    payload: RegionRequest,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> SegmentModel:
    """Move, resize, rename or relink a marked part (the author's own markup)."""
    segment = await media.update_region(
        scope,
        scope.workspace_id or "",
        project_id,
        asset_id,
        version_id,
        segment_id,
        x=payload.x,
        y=payload.y,
        width=payload.width,
        height=payload.height,
        label=payload.label,
        entity_id=payload.entity_id,
    )
    return _segment(segment)


@router.post("/assets/{asset_id}/versions/{version_id}/segments/{segment_id}/retire")
async def retire_region(
    project_id: str,
    asset_id: str,
    version_id: str,
    segment_id: str,
    scope: AccessScope = Depends(require_project_scope),
    media: MediaService = Depends(get_media_service),
) -> SegmentModel:
    segment = await media.retire_region(
        scope, scope.workspace_id or "", project_id, asset_id, version_id, segment_id
    )
    return _segment(segment)
