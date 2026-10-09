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
    AssetLink,
    LinkRole,
    MediaAsset,
    MediaAssetVersion,
    is_link_role,
)
from backend.domain.operations import CanonOperation, LinkAssetOp, RetireAssetLinkOp
from backend.domain.tenancy import Permission
from backend.ports import CanonRepositoryPort, MediaStorePort, ObjectStorePort
from backend.services.mutation_service import CommitResult, MutationService


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
            workspace_id=workspace_id, project_id=project_id, name=name
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
        )
        if retired is None:
            raise NotFoundError("Asset version not found.")
        return retired

    # ------------------------------------------------------------------- links
    async def list_links(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        asset_id: str | None = None,
        include_retired: bool = False,
    ) -> list[AssetLink]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.list_links(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_id=asset_id,
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
