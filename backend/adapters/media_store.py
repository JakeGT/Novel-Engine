"""PostgreSQL media identity store (Phase 2).

Holds asset identity and immutable version metadata only; bytes live in the scoped
object store under opaque keys. PostgreSQL is the lifecycle authority.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.adapters.system import uuid7
from backend.core.database import session_scope
from backend.domain.canon.ids import format_canon_id
from backend.domain.media import AssetLifecycle, MediaAsset, MediaAssetVersion


def _asset(row: Any) -> MediaAsset:
    return MediaAsset(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        name=row.name,
        lifecycle=AssetLifecycle(row.lifecycle),
        created_revision=row.created_revision,
        retired_revision=row.retired_revision,
        created_at=row.created_at,
    )


def _asset_version(row: Any) -> MediaAssetVersion:
    return MediaAssetVersion(
        id=row.id,
        asset_id=row.asset_id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        version=row.version,
        object_key=row.object_key,
        content_type=row.content_type,
        byte_length=row.byte_length,
        checksum=row.checksum,
        source=row.source,
        licence=row.licence,
        attribution=row.attribution,
        authority=row.authority,
        visibility=row.visibility,
        created_revision=row.created_revision,
        retired_revision=row.retired_revision,
        created_at=row.created_at,
    )


class PostgresMediaStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def create_asset(self, *, workspace_id: str, project_id: str, name: str) -> MediaAsset:
        asset_id = format_canon_id("asset", str(uuid7()))
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    "INSERT INTO media_assets (workspace_id, project_id, id, name, lifecycle, "
                    "created_revision) VALUES (:w, :p, :id, :name, 'active', 0)"
                ),
                {"w": workspace_id, "p": project_id, "id": asset_id, "name": name},
            )
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM media_assets WHERE workspace_id=:w AND project_id=:p "
                        "AND id=:id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": asset_id},
                )
            ).first()
        return _asset(row)

    async def get_asset(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> MediaAsset | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM media_assets WHERE workspace_id=:w AND project_id=:p "
                        "AND id=:id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": asset_id},
                )
            ).first()
        return _asset(row) if row else None

    async def list_assets(
        self, *, workspace_id: str, project_id: str, limit: int = 100, offset: int = 0
    ) -> list[MediaAsset]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM media_assets WHERE workspace_id=:w AND project_id=:p "
                        "ORDER BY created_at DESC, id LIMIT :limit OFFSET :offset"
                    ),
                    {"w": workspace_id, "p": project_id, "limit": limit, "offset": offset},
                )
            ).all()
        return [_asset(row) for row in rows]

    async def create_version(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        object_key: str,
        content_type: str,
        byte_length: int,
        checksum: str,
        source: str | None = None,
        licence: str | None = None,
        attribution: str | None = None,
        authority: str | None = None,
        visibility: str | None = None,
        created_revision: int = 0,
    ) -> MediaAssetVersion:
        version_id = format_canon_id("asset_version", str(uuid7()))
        async with session_scope(self._session_factory) as session:
            next_version = (
                await session.execute(
                    text(
                        "SELECT COALESCE(MAX(version), 0) + 1 FROM media_asset_versions "
                        "WHERE workspace_id=:w AND project_id=:p AND asset_id=:a"
                    ),
                    {"w": workspace_id, "p": project_id, "a": asset_id},
                )
            ).scalar()
            await session.execute(
                text(
                    "INSERT INTO media_asset_versions (workspace_id, project_id, id, asset_id, "
                    "version, object_key, content_type, byte_length, checksum, source, licence, "
                    "attribution, authority, visibility, created_revision) VALUES (:w, :p, :id, "
                    ":a, :v, :key, :ct, :bl, :sum, :source, :licence, :attribution, :authority, "
                    ":visibility, :rev)"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "id": version_id,
                    "a": asset_id,
                    "v": int(next_version or 1),
                    "key": object_key,
                    "ct": content_type,
                    "bl": byte_length,
                    "sum": checksum,
                    "source": source,
                    "licence": licence,
                    "attribution": attribution,
                    "authority": authority,
                    "visibility": visibility,
                    "rev": created_revision,
                },
            )
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM media_asset_versions WHERE workspace_id=:w AND "
                        "project_id=:p AND id=:id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": version_id},
                )
            ).first()
        return _asset_version(row)

    async def get_version(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> MediaAssetVersion | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM media_asset_versions WHERE workspace_id=:w AND "
                        "project_id=:p AND id=:id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": version_id},
                )
            ).first()
        return _asset_version(row) if row else None

    async def list_versions(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaAssetVersion]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM media_asset_versions WHERE workspace_id=:w AND "
                        "project_id=:p AND asset_id=:a ORDER BY version"
                    ),
                    {"w": workspace_id, "p": project_id, "a": asset_id},
                )
            ).all()
        return [_asset_version(row) for row in rows]

    async def retire_asset(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        retired_revision: int | None = None,
    ) -> MediaAsset | None:
        async with session_scope(self._session_factory) as session:
            result = await session.execute(
                text(
                    "UPDATE media_assets SET lifecycle='retired', retired_revision=:rev "
                    "WHERE workspace_id=:w AND project_id=:p AND id=:id"
                ),
                {"w": workspace_id, "p": project_id, "id": asset_id, "rev": retired_revision},
            )
            if not result.rowcount:  # type: ignore[attr-defined]
                return None
        return await self.get_asset(
            workspace_id=workspace_id, project_id=project_id, asset_id=asset_id
        )

    async def retire_version(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        retired_revision: int | None = None,
    ) -> MediaAssetVersion | None:
        async with session_scope(self._session_factory) as session:
            result = await session.execute(
                text(
                    "UPDATE media_asset_versions SET retired_revision=:rev "
                    "WHERE workspace_id=:w AND project_id=:p AND id=:id"
                ),
                {"w": workspace_id, "p": project_id, "id": version_id, "rev": retired_revision},
            )
            if not result.rowcount:  # type: ignore[attr-defined]
                return None
        return await self.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )

    async def import_asset(self, asset: MediaAsset) -> MediaAsset:
        """Insert an archived asset with its preserved id (idempotent)."""
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    "INSERT INTO media_assets (workspace_id, project_id, id, name, lifecycle, "
                    "created_revision, retired_revision, created_at) VALUES (:w, :p, :id, :name, "
                    ":lifecycle, :rev, :rrev, :created_at) ON CONFLICT DO NOTHING"
                ),
                {
                    "w": asset.workspace_id,
                    "p": asset.project_id,
                    "id": asset.id,
                    "name": asset.name,
                    "lifecycle": asset.lifecycle.value,
                    "rev": asset.created_revision,
                    "rrev": asset.retired_revision,
                    "created_at": asset.created_at,
                },
            )
        created = await self.get_asset(
            workspace_id=asset.workspace_id, project_id=asset.project_id, asset_id=asset.id
        )
        return created or asset

    async def import_version(self, version: MediaAssetVersion) -> MediaAssetVersion:
        """Insert an archived asset version with its preserved id (idempotent)."""
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    "INSERT INTO media_asset_versions (workspace_id, project_id, id, asset_id, "
                    "version, object_key, content_type, byte_length, checksum, source, licence, "
                    "attribution, authority, visibility, created_revision, retired_revision, "
                    "created_at) VALUES (:w, :p, :id, :a, :v, :key, :ct, :bl, :sum, :source, "
                    ":licence, :attribution, :authority, :visibility, :rev, :rrev, :created_at) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "w": version.workspace_id,
                    "p": version.project_id,
                    "id": version.id,
                    "a": version.asset_id,
                    "v": version.version,
                    "key": version.object_key,
                    "ct": version.content_type,
                    "bl": version.byte_length,
                    "sum": version.checksum,
                    "source": version.source,
                    "licence": version.licence,
                    "attribution": version.attribution,
                    "authority": version.authority,
                    "visibility": version.visibility,
                    "rev": version.created_revision,
                    "rrev": version.retired_revision,
                    "created_at": version.created_at,
                },
            )
        created = await self.get_version(
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            version_id=version.id,
        )
        return created or version

    async def health(self) -> bool:
        try:
            async with session_scope(self._session_factory) as session:
                await session.execute(text("SELECT 1 FROM media_assets LIMIT 1"))
            return True
        except Exception:
            return False
