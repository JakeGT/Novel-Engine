"""Media identity and explicit evidence-link application service (2I).

Registration (asset/version metadata) is operator/administrator metadata and is not
a canon mutation. Link acceptance and retirement ARE canon mutations: they build
typed operations and commit through the atomic mutation path, so they are revisioned and
audited. Bytes remain in the scoped object store; Phase 2 only records identity and
explicit links (no processing/scanning).

Since batch 6 (AST-003, AST-006, AST-008, OPS-007; images only) every stored picture
version is prepared once by a ``media_process`` job: decoded in a separate, limited
process (``backend/adapters/image_processor.py``) into a 256px thumbnail and a 1024px
web copy with no metadata, plus its whole-picture segment. The job is keyed by the
version's checksum and the processor version, so running it again changes nothing.
Uploads are refused in plain words when the workspace's storage is full. Retention
removes stored files no record refers to (after a grace period) and the copies of
retired versions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from backend.adapters.image_processor import (
    PROCESSOR_VERSION,
    VARIANT_CONTENT_TYPE,
    VARIANT_SIZES,
    ImageRejected,
)
from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.core.metrics import metrics
from backend.domain.canon.plans import PlanKind
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope, AuthMode, utcnow
from backend.domain.image_metadata import ImageMetadataError, strip_metadata
from backend.domain.media import (
    PROCESSING_FAILURES as _FAILURES,
)
from backend.domain.media import (
    AssetLifecycle,
    AssetLink,
    LinkRole,
    MediaAsset,
    MediaAssetVersion,
    MediaLifecycleEvent,
    MediaProcessing,
    MediaSegment,
    MediaVariant,
    ProcessingStatus,
    SegmentKind,
    StorageQuotaExceeded,
    VariantKind,
    is_link_role,
    processing_failure_words,
)
from backend.domain.operations import CanonOperation, LinkAssetOp, RetireAssetLinkOp
from backend.domain.tenancy import Permission
from backend.ports import (
    CanonRepositoryPort,
    MediaProcessingStorePort,
    MediaStorePort,
    ObjectStorePort,
)
from backend.services.mutation_service import CommitResult, MutationService

MAX_PICTURE_BYTES = 8 * 1024 * 1024
PICTURE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
# A picture shown on a scene links to the plan item that is the scene.
SCENE_TARGET = "plan_item"
MAX_LIBRARY = 200
MAX_LIBRARY_LINKS = 2000
MEDIA_PROCESS_KIND = "media_process"
DEFAULT_QUOTA_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_ORPHAN_GRACE = timedelta(hours=24)
# The types of file this service itself stores: pictures and their smaller copies.
_OWNED_TYPES = PICTURE_TYPES | {VARIANT_CONTENT_TYPE}

# Called with (workspace_id, project_id, version_ids) when picture versions are retired,
# discarded or their picture is retired, so derived indexes (vectors) can forget them.
VersionsRemovedHook = Callable[[str, str, list[str]], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibraryPicture:
    """A picture the author can choose: its newest current version, and where it is shown."""

    asset: MediaAsset
    version: MediaAssetVersion
    # How many entries and scenes show it now; 0 when it is shown nowhere.
    shown: int
    # Its processing state, when processing is set up; None before it has been queued.
    processing: MediaProcessing | None = None
    has_thumbnail: bool = False


@dataclass(frozen=True)
class ProcessingView:
    """What the author is told about one picture version's preparation."""

    status: ProcessingStatus | None
    failure_code: str | None
    message: str | None
    retryable: bool
    attempts: int
    width: int | None
    height: int | None
    copies: tuple[str, ...]
    processor_version: str | None
    current: bool


@dataclass
class MaintenanceReport:
    """Counts from a reconciliation or retention run; never names or content."""

    queued: int = 0
    orphans_found: int = 0
    orphans_removed: int = 0
    orphan_bytes: int = 0
    retired_copies_removed: int = 0
    projects: int = 0
    skipped: dict[str, int] = field(default_factory=dict)


def sniff_image_type(data: bytes) -> str | None:
    """The image type shown by the file's own leading bytes, or ``None``."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class MediaService:
    def __init__(
        self,
        *,
        media: MediaStorePort,
        objects: ObjectStorePort,
        mutation: MutationService,
        reader: CanonRepositoryPort,
        journal: object | None = None,
        tenancy: object | None = None,
        local_mode: bool = False,
        processing: MediaProcessingStorePort | None = None,
        processor: Any = None,
        runner: Any = None,
        quota_bytes: int = DEFAULT_QUOTA_BYTES,
        orphan_grace: timedelta = DEFAULT_ORPHAN_GRACE,
    ) -> None:
        self._media = media
        self._objects = objects
        self._mutation = mutation
        self._reader = reader
        self._journal = journal
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._processing = processing
        self._processor = processor
        self._runner = runner
        self._quota_bytes = max(0, int(quota_bytes))
        self._orphan_grace = orphan_grace
        self._removed_hooks: list[VersionsRemovedHook] = []
        # One lock per workspace around "is there room?" and the write that takes it.
        self._quota_locks: dict[str, asyncio.Lock] = {}

    # -------------------------------------------------------------- hooks (AST-008)
    def on_versions_removed(self, callback: VersionsRemovedHook) -> None:
        """Be told when picture versions stop being usable: a version retired, its
        picture retired, or an upload discarded. For derived indexes (vectors) to forget
        them. A failing callback is logged and never undoes the removal."""
        self._removed_hooks.append(callback)

    async def _versions_removed(
        self, workspace_id: str, project_id: str, version_ids: list[str]
    ) -> None:
        if not version_ids:
            return
        for callback in self._removed_hooks:
            try:
                await callback(workspace_id, project_id, list(version_ids))
            except Exception as error:  # noqa: BLE001 - cleanup never undoes a retirement
                log_event(
                    logger,
                    "media.removed_hook_failed",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                    versions=len(version_ids),
                )

    @property
    def processing_enabled(self) -> bool:
        return self._processing is not None and self._processor is not None

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ assets
    async def register_asset(
        self, scope: AccessScope, workspace_id: str, project_id: str, *, name: str
    ) -> MediaAsset:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        if not name.strip():
            raise ValidationError("Asset name is required.")
        return await self._media.create_asset(
            workspace_id=workspace_id,
            project_id=project_id,
            name=name,
            actor_user_id=scope.actor_user_id,
        )

    async def list_assets(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[MediaAsset]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._media.list_assets(workspace_id=workspace_id, project_id=project_id)

    async def get_asset(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> MediaAsset:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        if asset is None:
            raise NotFoundError("Asset not found.")
        return asset

    # ---------------------------------------------------------------- versions
    async def register_version(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        *,
        object_key: str,
        source: str | None = None,
        licence: str | None = None,
        attribution: str | None = None,
        authority: str | None = None,
        visibility: str | None = None,
    ) -> MediaAssetVersion:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        if asset is None:
            raise NotFoundError("Asset not found.")
        stat = self._objects.stat(object_key, workspace_id=workspace_id, project_id=project_id)
        if stat is None:
            raise ValidationError("Object not found in this project's object store.")
        # A registered version takes room just as an upload does.
        async with self._quota_guard(workspace_id):
            await self._check_quota(workspace_id, stat.byte_length)
            version = await self._media.create_version(
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset_id,
                object_key=object_key,
                content_type=stat.content_type,
                byte_length=stat.byte_length,
                checksum=stat.checksum,
                source=source,
                licence=licence,
                attribution=attribution,
                authority=authority,
                visibility=visibility,
                created_revision=await self._retired_revision(workspace_id, project_id) or 0,
                actor_user_id=scope.actor_user_id,
            )
        await self._queue_quietly(scope, workspace_id, project_id, version)
        return version

    async def list_lifecycle(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaLifecycleEvent]:
        """Who registered, imported, or retired this asset and its versions, and when."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        await self.get_asset(scope, workspace_id, project_id, asset_id)
        return await self._media.list_lifecycle_events(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )

    async def list_versions(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaAssetVersion]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._media.list_versions(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )

    async def get_version(
        self, scope: AccessScope, workspace_id: str, project_id: str, version_id: str
    ) -> MediaAssetVersion:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if version is None:
            raise NotFoundError("Asset version not found.")
        return version

    async def _retired_revision(self, workspace_id: str, project_id: str) -> int | None:
        if self._journal is None:
            return None
        branch = await self._journal.working_branch(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        return await self._reader.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )

    async def retire_asset(
        self, scope: AccessScope, workspace_id: str, project_id: str, asset_id: str
    ) -> MediaAsset:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        revision = await self._retired_revision(workspace_id, project_id)
        retired = await self._media.retire_asset(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_id=asset_id,
            retired_revision=revision,
            actor_user_id=scope.actor_user_id,
        )
        if retired is None:
            raise NotFoundError("Asset not found.")
        versions = await self._media.list_versions(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        await self._versions_removed(workspace_id, project_id, [v.id for v in versions])
        return retired

    async def retire_version(
        self, scope: AccessScope, workspace_id: str, project_id: str, version_id: str
    ) -> MediaAssetVersion:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_ADMIN)
        revision = await self._retired_revision(workspace_id, project_id)
        retired = await self._media.retire_version(
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=version_id,
            retired_revision=revision,
            actor_user_id=scope.actor_user_id,
        )
        if retired is None:
            raise NotFoundError("Asset version not found.")
        await self._versions_removed(workspace_id, project_id, [version_id])
        return retired

    # ---------------------------------------------------------------- pictures
    async def add_picture(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        entity_id: str,
        data: bytes,
        name: str | None,
        expected_revision: int,
    ) -> tuple[CommitResult, AssetLink, MediaAssetVersion]:
        """Store an image and link it to an entry as a picture of it.

        Any member who can write may do this. The type is decided from the bytes,
        never from what the upload claims, and only still images are accepted.
        Where and when it was taken, the device and any other hidden details are
        removed before it is stored, keeping only which way up it goes (AST-006).
        Nothing is decoded here or sent to a model: a ``media_process`` job prepares
        its smaller copies afterwards, in a separate, limited process.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        data, content_type = await self._checked_picture(data)
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        if entity is None or not entity.is_active:
            raise NotFoundError("Entry not found.")
        return await self._store_and_link(
            scope,
            workspace_id,
            project_id,
            data=data,
            content_type=content_type,
            label=" ".join((name or "").split())[:120] or f"Picture of {entity.name}",
            target_record_type="entity",
            target_id=entity.id,
            expected_revision=expected_revision,
        )

    async def add_scene_picture(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        scene_id: str,
        data: bytes,
        name: str | None,
        expected_revision: int,
    ) -> tuple[CommitResult, AssetLink, MediaAssetVersion]:
        """Store an image and show it on a scene of the plan (AST-EXP-018).

        The same checks as a picture of an entry. The link points at the plan item
        (``target_record_type="plan_item"``) with the role ``DEPICTS``.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        data, content_type = await self._checked_picture(data)
        scene = await self._reader.get_plan_item(
            workspace_id=workspace_id, project_id=project_id, plan_id=scene_id
        )
        if scene is None or not scene.is_active or scene.kind != PlanKind.SCENE:
            raise NotFoundError("Scene not found.")
        return await self._store_and_link(
            scope,
            workspace_id,
            project_id,
            data=data,
            content_type=content_type,
            label=" ".join((name or "").split())[:120] or f"Picture for {scene.title}"[:120],
            target_record_type=SCENE_TARGET,
            target_id=scene.id,
            expected_revision=expected_revision,
        )

    async def _checked_picture(self, data: bytes) -> tuple[bytes, str]:
        """The upload as it will be stored, and its type from its own bytes."""
        if not data:
            raise ValidationError("The picture is empty.")
        if len(data) > MAX_PICTURE_BYTES:
            raise ValidationError(
                f"The picture is too large (the limit is {MAX_PICTURE_BYTES // (1024 * 1024)} MB)."
            )
        content_type = sniff_image_type(data)
        if content_type is None:
            raise ValidationError("Only PNG, JPEG, and WebP pictures can be added.")
        try:
            # Walking a large file's structure takes a moment: off the event loop.
            data = await asyncio.to_thread(strip_metadata, data, content_type)
        except ImageMetadataError as error:
            raise ValidationError(
                "This picture could not be read. Save it again as a PNG, JPEG, or WebP "
                "file and add it once more."
            ) from error
        return data, content_type

    async def _store_and_link(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        data: bytes,
        content_type: str,
        label: str,
        target_record_type: str,
        target_id: str,
        expected_revision: int,
    ) -> tuple[CommitResult, AssetLink, MediaAssetVersion]:
        # Checked and counted together: the room is only taken up once the version row
        # exists, so two uploads at once must not both see the room as free.
        async with self._quota_guard(workspace_id):
            await self._check_quota(workspace_id, len(data))
            stat = self._objects.put(
                data, workspace_id=workspace_id, project_id=project_id, content_type=content_type
            )
            asset = await self._media.create_asset(
                workspace_id=workspace_id,
                project_id=project_id,
                name=label,
                actor_user_id=scope.actor_user_id,
            )
            version = await self._media.create_version(
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset.id,
                object_key=stat.key,
                content_type=stat.content_type,
                byte_length=stat.byte_length,
                checksum=stat.checksum,
                authority="author_reference",
                created_revision=await self._retired_revision(workspace_id, project_id) or 0,
                actor_user_id=scope.actor_user_id,
            )
        link_id = format_asset_link_id()
        try:
            result = await self._commit(
                scope,
                workspace_id,
                project_id,
                LinkAssetOp(
                    id=link_id,
                    asset_id=asset.id,
                    asset_version_id=version.id,
                    target_record_type=target_record_type,
                    target_id=target_id,
                    role=LinkRole.DEPICTS.value,
                ),
                expected_revision,
                "",
            )
        except Exception:
            # The picture never reached the story: leave nothing behind for the
            # library to offer.
            await self._discard_upload(workspace_id, project_id, link_id, asset.id, stat.key)
            raise
        link = await self._reader.get_link(
            workspace_id=workspace_id, project_id=project_id, link_id=link_id
        )
        assert link is not None
        await self._queue_quietly(scope, workspace_id, project_id, version)
        return result, link, version

    async def _discard_upload(
        self, workspace_id: str, project_id: str, link_id: str, asset_id: str, object_key: str
    ) -> None:
        """Remove an upload whose link was refused; a failure here never hides why."""
        try:
            # Checked first, in case the commit did land before it failed.
            if (
                await self._reader.get_link(
                    workspace_id=workspace_id, project_id=project_id, link_id=link_id
                )
                is not None
            ):
                return
            versions = await self._media.list_versions(
                workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
            )
            await self._media.discard_asset(
                workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
            )
            self._objects.delete(object_key, workspace_id=workspace_id, project_id=project_id)
            await self._versions_removed(workspace_id, project_id, [v.id for v in versions])
        except Exception:  # noqa: BLE001 - the refusal is what the author needs to see
            logger.warning("media.discard_failed", exc_info=True)

    async def picture_library(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[LibraryPicture]:
        """Every picture in this line of the story that can still be shown, newest first.

        Pictures no longer shown anywhere are included: removing a picture from an
        entry or a scene retires the link, never the picture, so it can be used again.
        Only pictures linked at some time in the line being worked in are offered, so
        a picture added in a what-if stays out of the main story. A picture on a scene
        taken out of the plan is not counted as shown. Each picture is its newest
        version that is still current.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        assets = await self._media.list_assets(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_LIBRARY
        )
        links = await self._reader.list_links(
            workspace_id=workspace_id,
            project_id=project_id,
            include_retired=True,
            limit=MAX_LIBRARY_LINKS,
        )
        linked = {link.asset_id for link in links}
        active = [link for link in links if link.is_active]
        retired_scenes: set[str] = set()
        if any(link.target_record_type == SCENE_TARGET for link in active):
            retired_scenes = {
                item.id
                for item in await self._reader.list_plan_items(
                    workspace_id=workspace_id, project_id=project_id, include_retired=True
                )
                if not item.is_active
            }
        shown: dict[str, int] = {}
        for link in active:
            if link.target_record_type == SCENE_TARGET and link.target_record_id in retired_scenes:
                continue
            shown[link.asset_id] = shown.get(link.asset_id, 0) + 1
        candidates = [asset for asset in assets if asset.is_active and asset.id in linked]
        versions = await self._media.list_versions_for_assets(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_ids=[asset.id for asset in candidates],
        )
        newest_of: list[tuple[MediaAsset, MediaAssetVersion]] = []
        for asset in candidates:
            current = [
                version
                for version in versions.get(asset.id, [])
                if version.retired_revision is None and version.content_type in PICTURE_TYPES
            ]
            if not current:
                continue
            newest_of.append((asset, max(current, key=lambda version: version.version)))
        states: dict[str, MediaProcessing] = {}
        copies: dict[str, list[MediaVariant]] = {}
        if self._processing is not None and newest_of:
            ids = [version.id for _asset, version in newest_of]
            states = await self._processing.list_processing(
                workspace_id=workspace_id, project_id=project_id, version_ids=ids
            )
            copies = await self._processing.list_variants(
                workspace_id=workspace_id, project_id=project_id, version_ids=ids
            )
        return [
            LibraryPicture(
                asset=asset,
                version=version,
                shown=shown.get(asset.id, 0),
                processing=states.get(version.id),
                has_thumbnail=any(
                    copy.kind is VariantKind.THUMBNAIL for copy in copies.get(version.id, [])
                ),
            )
            for asset, version in newest_of
        ]

    async def open_content(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
    ) -> tuple[bytes, str]:
        """The stored bytes of one media version, for members of the project only."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._servable_version(workspace_id, project_id, asset_id, version_id)
        try:
            data = self._objects.open(
                version.object_key, workspace_id=workspace_id, project_id=project_id
            )
        except Exception as error:  # noqa: BLE001 - a missing object is a missing picture
            raise NotFoundError("Media not found.") from error
        return data, version.content_type

    async def open_picture(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
        *,
        copy: VariantKind | None,
    ) -> tuple[bytes, str, str]:
        """A picture to show: its smaller copy when asked for and made, else the
        original. Returns the bytes, their type, and which was served (the copy's kind
        or ``original``). Members of the project only; the same checks as the original."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._servable_version(workspace_id, project_id, asset_id, version_id)
        if copy is not None and self._processing is not None:
            variant = await self._processing.get_variant(
                workspace_id=workspace_id, project_id=project_id, version_id=version.id, kind=copy
            )
            if variant is not None and variant.source_checksum == version.checksum:
                try:
                    data = self._objects.open(
                        variant.object_key, workspace_id=workspace_id, project_id=project_id
                    )
                    return data, variant.content_type, variant.kind.value
                except Exception:  # noqa: BLE001 - a lost copy falls back to the original
                    log_event(logger, "media.copy_missing", level=logging.WARNING)
        try:
            data = self._objects.open(
                version.object_key, workspace_id=workspace_id, project_id=project_id
            )
        except Exception as error:  # noqa: BLE001 - a missing object is a missing picture
            raise NotFoundError("Media not found.") from error
        return data, version.content_type, "original"

    async def _servable_version(
        self, workspace_id: str, project_id: str, asset_id: str, version_id: str
    ) -> MediaAssetVersion:
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if version is None or version.asset_id != asset_id or version.retired_revision is not None:
            raise NotFoundError("Media not found.")
        asset = await self._media.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )
        if asset is None or asset.lifecycle is not AssetLifecycle.ACTIVE:
            raise NotFoundError("Media not found.")
        # Only types this application stored itself are served, and only as images.
        if version.content_type not in PICTURE_TYPES:
            raise NotFoundError("Media not found.")
        return version

    # ------------------------------------------------------- processing (AST-003)
    def register_jobs(self) -> None:
        """Teach the job runner to prepare pictures."""
        if self._runner is not None and self.processing_enabled:
            self._runner.register(MEDIA_PROCESS_KIND, self._run_process_job)

    async def storage_used(self, workspace_id: str) -> int:
        if self._processing is None:
            return 0
        return await self._processing.workspace_bytes(workspace_id=workspace_id)

    def _quota_guard(self, workspace_id: str) -> asyncio.Lock:
        """Held from the room check until the new version row is written, so uploads to
        one workspace at the same time are counted one after another. This covers one
        API process (how the application is run); several processes would need a
        database lock here as well. An archive restore does not take it and may go over
        the room: the author's own archive is restored whole, and the next upload is
        then refused in the usual plain words."""
        lock = self._quota_locks.get(workspace_id)
        if lock is None:
            lock = self._quota_locks[workspace_id] = asyncio.Lock()
        return lock

    async def _check_quota(self, workspace_id: str, incoming: int) -> None:
        """Refuse, in plain words, a picture that would take the workspace over its room."""
        if self._quota_bytes <= 0 or self._processing is None:
            return
        used = await self._processing.workspace_bytes(workspace_id=workspace_id)
        # Room is kept for the smaller copies too: they are a fraction of the original.
        if used + incoming + incoming // 4 > self._quota_bytes:
            metrics.increment(
                "media_uploads_refused_total",
                "Picture uploads refused, by reason.",
                reason="storage_full",
            )
            raise StorageQuotaExceeded(
                "There is no room left for this picture: this workspace is using "
                f"{_size(used)} of its {_size(self._quota_bytes)}. Remove pictures you no "
                "longer need, or ask whoever runs this site for more room."
            )

    async def _queue_quietly(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        version: MediaAssetVersion,
    ) -> None:
        """Queue a new picture for preparation; a failure here never undoes the upload
        (the backfill finds it later)."""
        if not self.processing_enabled or version.content_type not in PICTURE_TYPES:
            return
        try:
            await self._queue(scope, workspace_id, project_id, version, attempt=0)
        except Exception as error:  # noqa: BLE001 - the picture itself is stored and linked
            log_event(
                logger,
                "media.queue_failed",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )

    async def _queue(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        version: MediaAssetVersion,
        *,
        attempt: int,
    ) -> MediaProcessing:
        assert self._processing is not None
        job_id: str | None = None
        if self._runner is not None:
            from backend.services.job_runner import JobSubmission

            # Keyed by the bytes and the processor: asking again is the same job.
            key = f"media:{version.id}:{version.checksum[:16]}:{PROCESSOR_VERSION}:{attempt}"
            job = await self._runner.submit(
                scope,
                workspace_id,
                project_id,
                JobSubmission(
                    kind=MEDIA_PROCESS_KIND,
                    payload={
                        "asset_id": version.asset_id,
                        "asset_version_id": version.id,
                        "source_checksum": version.checksum,
                        "processor_version": PROCESSOR_VERSION,
                    },
                    idempotency_key=key[:200],
                ),
            )
            job_id = job.id
        return await self._processing.queue(
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=version.id,
            asset_id=version.asset_id,
            checksum=version.checksum,
            processor_version=PROCESSOR_VERSION,
            job_id=job_id,
        )

    async def processing_status(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
    ) -> ProcessingView:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._version_of(workspace_id, project_id, asset_id, version_id)
        return await self._view(workspace_id, project_id, version)

    async def _view(
        self, workspace_id: str, project_id: str, version: MediaAssetVersion
    ) -> ProcessingView:
        state = None
        copies: list[MediaVariant] = []
        if self._processing is not None:
            state = await self._processing.get_processing(
                workspace_id=workspace_id, project_id=project_id, version_id=version.id
            )
            copies = (
                await self._processing.list_variants(
                    workspace_id=workspace_id, project_id=project_id, version_ids=[version.id]
                )
            ).get(version.id, [])
        if state is None:
            return ProcessingView(
                status=None,
                failure_code=None,
                message=None,
                retryable=self.processing_enabled and version.content_type in PICTURE_TYPES,
                attempts=0,
                width=None,
                height=None,
                copies=(),
                processor_version=None,
                current=False,
            )
        failed = state.status is ProcessingStatus.FAILED
        return ProcessingView(
            status=state.status,
            failure_code=state.failure_code if failed else None,
            message=processing_failure_words(state.failure_code) if failed else None,
            # Anything that failed can be tried again; some failures say it will not help.
            retryable=failed and self.processing_enabled,
            attempts=state.attempts,
            width=state.width,
            height=state.height,
            copies=tuple(sorted(copy.kind.value for copy in copies)),
            processor_version=state.processor_version,
            current=state.is_current(
                checksum=version.checksum, processor_version=PROCESSOR_VERSION
            ),
        )

    async def retry_processing(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
    ) -> ProcessingView:
        """Prepare a picture again after it failed (or was never prepared). A picture
        already prepared by this processor from these bytes is left as it is."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not self.processing_enabled:
            raise ValidationError("Pictures cannot be prepared on this installation.")
        version = await self._version_of(workspace_id, project_id, asset_id, version_id)
        if version.content_type not in PICTURE_TYPES:
            raise ValidationError("Only PNG, JPEG and WebP pictures are prepared.")
        assert self._processing is not None
        state = await self._processing.get_processing(
            workspace_id=workspace_id, project_id=project_id, version_id=version.id
        )
        busy = state is not None and state.status in (
            ProcessingStatus.QUEUED,
            ProcessingStatus.PROCESSING,
        )
        done = (
            state is not None
            and state.status is ProcessingStatus.READY
            and state.is_current(checksum=version.checksum, processor_version=PROCESSOR_VERSION)
        )
        if not busy and not done:
            attempt = (state.attempts if state is not None else 0) + 1
            await self._queue(scope, workspace_id, project_id, version, attempt=attempt)
        return await self._view(workspace_id, project_id, version)

    # ------------------------------------------------------ segments (AST-003)
    async def list_segments(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
    ) -> list[MediaSegment]:
        """The whole picture (once it is prepared) and the regions the author chose."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._version_of(workspace_id, project_id, asset_id, version_id)
        if self._processing is None:
            return []
        return list(
            await self._processing.list_segments(
                workspace_id=workspace_id, project_id=project_id, version_id=version.id
            )
        )

    async def add_region(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        label: str | None = None,
    ) -> MediaSegment:
        """Mark a part of a prepared picture (a face, a sign) so it can be pointed at on
        its own. Pixels of the picture as shown, inside its edges."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        version = await self._servable_version(workspace_id, project_id, asset_id, version_id)
        if self._processing is None:
            raise ValidationError("Pictures cannot be prepared on this installation.")
        state = await self._processing.get_processing(
            workspace_id=workspace_id, project_id=project_id, version_id=version.id
        )
        if (
            state is None
            or state.status is not ProcessingStatus.READY
            or state.width is None
            or state.height is None
        ):
            raise ValidationError("A part of a picture can be marked once the picture is ready.")
        if width < 1 or height < 1 or x < 0 or y < 0:
            raise ValidationError("A marked part needs a place and a size inside the picture.")
        if x + width > state.width or y + height > state.height:
            raise ValidationError(
                f"That part goes past the picture's edge ({state.width} by {state.height})."
            )
        clean = " ".join((label or "").split())[:120] or None
        region_hash = hashlib.sha256(
            f"{version.checksum}:{x}:{y}:{width}:{height}".encode()
        ).hexdigest()
        return await self._processing.add_segment(  # type: ignore[no-any-return]
            MediaSegment(
                workspace_id=workspace_id,
                project_id=project_id,
                id=f"seg_{uuid7()}",
                asset_id=version.asset_id,
                asset_version_id=version.id,
                kind=SegmentKind.REGION,
                x=x,
                y=y,
                width=width,
                height=height,
                label=clean,
                content_hash=region_hash,
                created_by=scope.actor_user_id,
            )
        )

    async def retire_region(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        version_id: str,
        segment_id: str,
    ) -> MediaSegment:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        await self._version_of(workspace_id, project_id, asset_id, version_id)
        retired = (
            await self._processing.retire_segment(
                workspace_id=workspace_id, project_id=project_id, segment_id=segment_id
            )
            if self._processing is not None
            else None
        )
        if retired is None or retired.asset_version_id != version_id:
            raise NotFoundError("That marked part was not found.")
        return retired  # type: ignore[no-any-return]

    async def export_regions(self, workspace_id: str, project_id: str) -> list[dict[str, Any]]:
        """The parts of pictures the author marked, for an archive (they are the author's;
        the whole-picture segments are rebuilt by processing and are left out)."""
        if self._processing is None:
            return []
        return [
            {
                "id": region.id,
                "asset_id": region.asset_id,
                "asset_version_id": region.asset_version_id,
                "x": region.x,
                "y": region.y,
                "width": region.width,
                "height": region.height,
                "label": region.label,
                "created_at": region.created_at.isoformat(),
            }
            for region in await self._processing.list_regions(
                workspace_id=workspace_id, project_id=project_id
            )
        ]

    async def restore_regions(self, workspace_id: str, project_id: str, regions: list[Any]) -> int:
        """Put back marked parts from an archive, for pictures that came back with it.
        A malformed entry, or one naming a picture not there, is skipped."""
        if self._processing is None or not isinstance(regions, list):
            return 0
        restored = 0
        for raw in regions:
            if not isinstance(raw, dict):
                continue
            try:
                version = await self._media.get_version(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    version_id=str(raw["asset_version_id"]),
                )
                if version is None or version.asset_id != raw["asset_id"]:
                    continue
                x, y, width, height = (int(raw[key]) for key in ("x", "y", "width", "height"))
                if min(x, y) < 0 or min(width, height) < 1 or not str(raw["id"]).startswith("seg_"):
                    continue
                label = " ".join(str(raw.get("label") or "").split())[:120] or None
                await self._processing.add_segment(
                    MediaSegment(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        id=str(raw["id"])[:80],
                        asset_id=version.asset_id,
                        asset_version_id=version.id,
                        kind=SegmentKind.REGION,
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        label=label,
                        content_hash=hashlib.sha256(
                            f"{version.checksum}:{x}:{y}:{width}:{height}".encode()
                        ).hexdigest(),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
            restored += 1
        return restored

    async def storage(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> dict[str, int]:
        """How much room the workspace's pictures take, and its limit (0 is none)."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return {
            "used_bytes": await self.storage_used(workspace_id),
            "quota_bytes": self._quota_bytes,
        }

    async def _version_of(
        self, workspace_id: str, project_id: str, asset_id: str, version_id: str
    ) -> MediaAssetVersion:
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if version is None or version.asset_id != asset_id:
            raise NotFoundError("Picture not found.")
        return version

    async def _run_process_job(self, ctx: Any) -> dict[str, Any]:
        job = ctx.job
        version_id = str(job.payload.get("asset_version_id") or "")
        if not version_id:
            raise ValidationError("This piece of work names no picture.")
        await ctx.step("checking", 5)
        return await self.process_version(job.workspace_id, job.project_id, version_id, ctx=ctx)

    async def process_version(
        self, workspace_id: str, project_id: str, version_id: str, *, ctx: Any = None
    ) -> dict[str, Any]:
        """Prepare one picture version: smaller copies, the whole-picture segment and its
        size. Idempotent: a version already prepared by this processor version from the
        same bytes is left untouched. Returns counts and fixed words only."""
        if self._processing is None or self._processor is None:
            return {"outcome": "unavailable"}
        version = await self._media.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        asset = (
            await self._media.get_asset(
                workspace_id=workspace_id, project_id=project_id, asset_id=version.asset_id
            )
            if version is not None
            else None
        )
        if version is None or asset is None or version.retired_revision is not None:
            return {"outcome": "gone"}
        if asset.lifecycle is not AssetLifecycle.ACTIVE:
            return {"outcome": "gone"}
        if version.content_type not in PICTURE_TYPES:
            return {"outcome": "not_a_picture"}
        state = await self._processing.get_processing(
            workspace_id=workspace_id, project_id=project_id, version_id=version.id
        )
        if (
            state is not None
            and state.status is ProcessingStatus.READY
            and state.is_current(checksum=version.checksum, processor_version=PROCESSOR_VERSION)
        ):
            copies = (
                await self._processing.list_variants(
                    workspace_id=workspace_id, project_id=project_id, version_ids=[version.id]
                )
            ).get(version.id, [])
            if {copy.kind.value for copy in copies} >= set(VARIANT_SIZES):
                self._count("already_done")
                return {"outcome": "already_done"}
        if state is None or not state.is_current(
            checksum=version.checksum, processor_version=PROCESSOR_VERSION
        ):
            await self._processing.queue(
                workspace_id=workspace_id,
                project_id=project_id,
                version_id=version.id,
                asset_id=version.asset_id,
                checksum=version.checksum,
                processor_version=PROCESSOR_VERSION,
                job_id=getattr(getattr(ctx, "job", None), "id", None),
            )
        await self._processing.start(
            workspace_id=workspace_id, project_id=project_id, version_id=version.id
        )
        started = utcnow()
        stored: list[str] = []
        try:
            try:
                data = self._objects.open(
                    version.object_key, workspace_id=workspace_id, project_id=project_id
                )
            except Exception:  # noqa: BLE001 - a missing original is a fixed failure
                return await self._failed(workspace_id, project_id, version.id, "missing_original")
            if hashlib.sha256(data).hexdigest() != version.checksum:
                return await self._failed(workspace_id, project_id, version.id, "damaged")
            if ctx is not None:
                await ctx.step("opening", 30)
            holding: AbstractAsyncContextManager[Any] = (
                ctx.holding() if ctx is not None else nullcontext()
            )
            try:
                async with holding:
                    processed = await self._processor.process(data)
            except ImageRejected as rejected:
                code = rejected.code if rejected.code in _FAILURES else "processor_failed"
                return await self._failed(workspace_id, project_id, version.id, code)
            if ctx is not None:
                await ctx.step("storing", 80)
            variants: list[MediaVariant] = []
            for rendition in processed.renditions:
                stat = self._objects.put(
                    rendition.data,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    content_type=rendition.content_type,
                )
                stored.append(stat.key)
                variants.append(
                    MediaVariant(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        asset_version_id=version.id,
                        kind=VariantKind(rendition.kind),
                        object_key=stat.key,
                        content_type=stat.content_type,
                        width=rendition.width,
                        height=rendition.height,
                        byte_length=stat.byte_length,
                        checksum=stat.checksum,
                        source_checksum=version.checksum,
                        processor_version=PROCESSOR_VERSION,
                    )
                )
            whole = MediaSegment(
                workspace_id=workspace_id,
                project_id=project_id,
                id=f"seg_{uuid7()}",
                asset_id=version.asset_id,
                asset_version_id=version.id,
                kind=SegmentKind.WHOLE,
                x=0,
                y=0,
                width=processed.width,
                height=processed.height,
                content_hash=version.checksum,
                processor_version=PROCESSOR_VERSION,
            )
            replaced = await self._processing.complete(
                workspace_id=workspace_id,
                project_id=project_id,
                version_id=version.id,
                width=processed.width,
                height=processed.height,
                detected_format=processed.detected_format,
                variants=variants,
                whole=whole,
            )
        except BaseException:
            # Nothing half-made is left behind: copies stored before the failure go.
            for key in stored:
                self._objects.delete(key, workspace_id=workspace_id, project_id=project_id)
            if not stored:
                # Unknown failures still leave a retryable, plain state (not "processing").
                try:
                    current = await self._processing.get_processing(
                        workspace_id=workspace_id, project_id=project_id, version_id=version.id
                    )
                    if current is not None and current.status is ProcessingStatus.PROCESSING:
                        await self._processing.fail(
                            workspace_id=workspace_id,
                            project_id=project_id,
                            version_id=version.id,
                            failure_code="processor_failed",
                        )
                except Exception:  # noqa: BLE001 - the original error is the one raised
                    pass
            raise
        for key in replaced:
            self._objects.delete(key, workspace_id=workspace_id, project_id=project_id)
        metrics.observe(
            "media_processing_seconds",
            "Time to prepare one picture, from start to stored copies.",
            (utcnow() - started).total_seconds(),
        )
        self._count("ready")
        return {
            "outcome": "ready",
            "width": processed.width,
            "height": processed.height,
            "copies": len(variants),
        }

    async def _failed(
        self, workspace_id: str, project_id: str, version_id: str, code: str
    ) -> dict[str, Any]:
        assert self._processing is not None
        await self._processing.fail(
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=version_id,
            failure_code=code,
        )
        self._count("failed", code)
        # The code only: never the file's name, its bytes or a decoder's words.
        log_event(logger, "media.process.failed", level=logging.WARNING, failure_code=code)
        return {"outcome": "failed", "failure_code": code}

    @staticmethod
    def _count(outcome: str, code: str = "none") -> None:
        metrics.increment(
            "media_processing_total",
            "Pictures prepared, by outcome and failure code.",
            outcome=outcome,
            failure_code=code,
        )

    # ------------------------------------------------ operations (OPS-007, AST-008)
    async def processing_lag(self) -> dict[str, Any] | None:
        """How many pictures wait to be prepared and the oldest wait; counts only."""
        if self._processing is None:
            return None
        return await self._processing.lag()

    async def queue_backfill(
        self,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        limit: int = 500,
        include_failed: bool = False,
        local_actor: str | None = None,
    ) -> MaintenanceReport:
        """Queue every current picture not yet prepared by this processor version (an
        operator command). Each is queued as the person who added it; on a local
        install as the local user."""
        report = MaintenanceReport()
        if not self.processing_enabled:
            report.skipped["processing_off"] = 1
            return report
        assert self._processing is not None
        pending = await self._processing.pending_versions(
            processor_version=PROCESSOR_VERSION,
            content_types=sorted(PICTURE_TYPES),
            workspace_id=workspace_id,
            project_id=project_id,
            limit=limit,
            include_failed=include_failed,
        )
        for item in pending:
            actor = local_actor if self._local_mode and local_actor else item.actor_user_id
            if not actor:
                report.skipped["no_actor"] = report.skipped.get("no_actor", 0) + 1
                continue
            version = await self._media.get_version(
                workspace_id=item.workspace_id,
                project_id=item.project_id,
                version_id=item.asset_version_id,
            )
            if version is None:
                continue
            scope = AccessScope(
                actor_user_id=actor,
                auth_mode=AuthMode.LOCAL if self._local_mode else AuthMode.HOSTED,
                correlation_id=f"media-backfill-{uuid7()}",
                workspace_id=item.workspace_id,
                project_id=item.project_id,
            )
            state = await self._processing.get_processing(
                workspace_id=item.workspace_id,
                project_id=item.project_id,
                version_id=item.asset_version_id,
            )
            try:
                await self._queue(
                    scope,
                    item.workspace_id,
                    item.project_id,
                    version,
                    attempt=(state.attempts if state is not None else 0) + 1,
                )
            except Exception as error:  # noqa: BLE001 - one refusal never stops the rest
                reason = type(error).__name__.lower()[:40]
                report.skipped[reason] = report.skipped.get(reason, 0) + 1
                continue
            report.queued += 1
        return report

    async def queue_project(self, scope: AccessScope, workspace_id: str, project_id: str) -> int:
        """Queue every picture of one story not yet prepared (after a restore)."""
        if not self.processing_enabled:
            return 0
        assert self._processing is not None
        pending = await self._processing.pending_versions(
            processor_version=PROCESSOR_VERSION,
            content_types=sorted(PICTURE_TYPES),
            workspace_id=workspace_id,
            project_id=project_id,
        )
        queued = 0
        for item in pending:
            version = await self._media.get_version(
                workspace_id=workspace_id, project_id=project_id, version_id=item.asset_version_id
            )
            if version is None:
                continue
            await self._queue_quietly(scope, workspace_id, project_id, version)
            queued += 1
        return queued

    async def variant_object_keys(self, workspace_id: str, project_id: str) -> set[str]:
        """Object keys that hold smaller copies (rebuildable; left out of an archive)."""
        if self._processing is None:
            return set()
        return await self._processing.variant_object_keys(
            workspace_id=workspace_id, project_id=project_id
        )

    async def reconcile_orphans(
        self,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        dry_run: bool = True,
        grace: timedelta | None = None,
    ) -> MaintenanceReport:
        """Find stored pictures no picture version or copy refers to, older than the grace
        period, and (unless ``dry_run``) remove them. A file newer than the grace period
        may be an upload still on its way, so it is never touched. Only picture files are
        considered: other kinds in the project's store are not this service's to remove."""
        report = MaintenanceReport()
        if self._processing is None:
            report.skipped["processing_off"] = 1
            return report
        cutoff = utcnow() - (grace if grace is not None else self._orphan_grace)
        if workspace_id is not None and project_id is not None:
            projects = [(workspace_id, project_id)]
        else:
            projects = [
                pair
                for pair in await self._processing.projects_with_media()
                if workspace_id is None or pair[0] == workspace_id
            ]
        for ws, pid in projects:
            report.projects += 1
            known = await self._processing.known_object_keys(workspace_id=ws, project_id=pid)
            for stat in self._objects.list_objects(workspace_id=ws, project_id=pid):
                if stat.key in known or stat.created_at > cutoff:
                    continue
                # Only files of the kinds this service writes (originals and copies) are
                # its own to remove; anything else in the project's store (a seeded
                # drawing, an export, a non-picture file an archive brought) is left alone.
                if stat.content_type not in _OWNED_TYPES:
                    continue
                report.orphans_found += 1
                report.orphan_bytes += stat.byte_length
                if not dry_run and self._objects.delete(stat.key, workspace_id=ws, project_id=pid):
                    report.orphans_removed += 1
        log_event(
            logger,
            "media.reconcile",
            projects=report.projects,
            orphans_found=report.orphans_found,
            orphans_removed=report.orphans_removed,
            dry_run=dry_run,
        )
        return report

    async def remove_retired_copies(
        self, *, dry_run: bool = True, limit: int = 1000
    ) -> MaintenanceReport:
        """Remove the smaller copies of retired versions and retired pictures. The
        original stays (its history is the author's); a copy can always be made again."""
        report = MaintenanceReport()
        if self._processing is None:
            report.skipped["processing_off"] = 1
            return report
        for ws, pid, version_id in await self._processing.retired_with_variants(limit=limit):
            if dry_run:
                copies = (
                    await self._processing.list_variants(
                        workspace_id=ws, project_id=pid, version_ids=[version_id]
                    )
                ).get(version_id, [])
                report.retired_copies_removed += len(copies)
                continue
            for key in await self._processing.remove_variants(
                workspace_id=ws, project_id=pid, version_id=version_id
            ):
                self._objects.delete(key, workspace_id=ws, project_id=pid)
                report.retired_copies_removed += 1
        return report

    # ------------------------------------------------------------------- links
    async def list_links(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        asset_id: str | None = None,
        target_id: str | None = None,
        include_retired: bool = False,
    ) -> list[AssetLink]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.list_links(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_id=asset_id,
            target_record_id=target_id,
            include_retired=include_retired,
        )

    async def create_link(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        asset_id: str,
        target_id: str,
        role: str = LinkRole.EVIDENCE_FOR.value,
        asset_version_id: str | None = None,
        target_record_type: str = "entity",
        expected_revision: int,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, AssetLink]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not is_link_role(role):
            raise ValidationError(f"Unknown link role {role!r}.")
        link_id = format_asset_link_id()
        operation = LinkAssetOp(
            id=link_id,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            target_record_type=target_record_type,
            target_id=target_id,
            role=role,
        )
        result = await self._commit(
            scope, workspace_id, project_id, operation, expected_revision, idempotency_key
        )
        link = await self._reader.get_link(
            workspace_id=workspace_id, project_id=project_id, link_id=link_id
        )
        assert link is not None
        return result, link

    async def retire_link(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        link_id: str,
        *,
        expected_revision: int,
    ) -> CommitResult:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        existing = await self._reader.get_link(
            workspace_id=workspace_id, project_id=project_id, link_id=link_id
        )
        if existing is None:
            raise NotFoundError("Asset link not found.")
        operation = RetireAssetLinkOp(id=link_id)
        return await self._commit(scope, workspace_id, project_id, operation, expected_revision, "")

    async def _commit(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        operation: CanonOperation,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommitResult:
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=[operation],
            expected_head=expected_revision,
            idempotency_key=idempotency_key,
        )
        return await self._mutation.commit(scope, mutation.id)


def _size(byte_count: int) -> str:
    """A size in plain words: "512 KB", "3.2 MB", "2 GB"."""
    if byte_count < 1024 * 1024:
        return f"{max(1, round(byte_count / 1024))} KB"
    if byte_count < 1024 * 1024 * 1024:
        return f"{byte_count / (1024 * 1024):.1f} MB".replace(".0 MB", " MB")
    return f"{byte_count / (1024 * 1024 * 1024):.1f} GB".replace(".0 GB", " GB")


def format_asset_link_id() -> str:
    from backend.domain.canon.ids import format_canon_id

    return format_canon_id("asset_link", str(uuid7()))


__all__ = [
    "MEDIA_PROCESS_KIND",
    "LibraryPicture",
    "MaintenanceReport",
    "MediaService",
    "ProcessingView",
    "VersionsRemovedHook",
]
