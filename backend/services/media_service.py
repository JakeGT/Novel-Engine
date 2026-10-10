"""Media identity and explicit evidence-link application service (2I).

Registration (asset/version metadata) is operator/administrator metadata and is not
a canon mutation. Link acceptance and retirement ARE canon mutations: they build
typed operations and commit through the atomic mutation path, so they are revisioned and
audited. Bytes remain in the scoped object store; Phase 2 only records identity and
explicit links (no processing/scanning).
"""

from __future__ import annotations

from backend.adapters.system import uuid7
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.media import (
    AssetLifecycle,
    AssetLink,
    LinkRole,
    MediaAsset,
    MediaAssetVersion,
    MediaLifecycleEvent,
    is_link_role,
)
from backend.domain.operations import CanonOperation, LinkAssetOp, RetireAssetLinkOp
from backend.domain.tenancy import Permission
from backend.ports import CanonRepositoryPort, MediaStorePort, ObjectStorePort
from backend.services.mutation_service import CommitResult, MutationService

MAX_PICTURE_BYTES = 8 * 1024 * 1024
PICTURE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


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
    ) -> None:
        self._media = media
        self._objects = objects
        self._mutation = mutation
        self._reader = reader
        self._journal = journal
        self._tenancy = tenancy
        self._local_mode = local_mode

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
        return await self._media.create_version(
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
        branch = await self._journal.ensure_root_branch(  # type: ignore[attr-defined]
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
        Nothing is decoded, resized, or sent to a model.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not data:
            raise ValidationError("The picture is empty.")
        if len(data) > MAX_PICTURE_BYTES:
            raise ValidationError(
                f"The picture is too large (the limit is {MAX_PICTURE_BYTES // (1024 * 1024)} MB)."
            )
        content_type = sniff_image_type(data)
        if content_type is None:
            raise ValidationError("Only PNG, JPEG, and WebP pictures can be added.")
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        if entity is None or not entity.is_active:
            raise NotFoundError("Entry not found.")

        stat = self._objects.put(
            data, workspace_id=workspace_id, project_id=project_id, content_type=content_type
        )
        label = " ".join((name or "").split())[:120] or f"Picture of {entity.name}"
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
        result = await self._commit(
            scope,
            workspace_id,
            project_id,
            LinkAssetOp(
                id=link_id,
                asset_id=asset.id,
                asset_version_id=version.id,
                target_record_type="entity",
                target_id=entity.id,
                role=LinkRole.DEPICTS.value,
            ),
            expected_revision,
            "",
        )
        link = await self._reader.get_link(
            workspace_id=workspace_id, project_id=project_id, link_id=link_id
        )
        assert link is not None
        return result, link, version

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
        try:
            data = self._objects.open(
                version.object_key, workspace_id=workspace_id, project_id=project_id
            )
        except Exception as error:  # noqa: BLE001 - a missing object is a missing picture
            raise NotFoundError("Media not found.") from error
        return data, version.content_type

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


def format_asset_link_id() -> str:
    from backend.domain.canon.ids import format_canon_id

    return format_canon_id("asset_link", str(uuid7()))


__all__ = ["MediaService"]
