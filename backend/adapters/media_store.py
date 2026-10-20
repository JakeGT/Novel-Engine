"""PostgreSQL media identity store (Phase 2).

Holds asset identity and immutable version metadata only; bytes live in the scoped
object store under opaque keys. PostgreSQL is the lifecycle authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from backend.adapters.system import uuid7
from backend.core.database import session_scope
from backend.domain.canon.ids import format_canon_id
from backend.domain.media import (
    AssetLifecycle,
    MediaAsset,
    MediaAssetVersion,
    MediaLifecycleAction,
    MediaLifecycleEvent,
    MediaProcessing,
    MediaSegment,
    MediaVariant,
    ProcessingStatus,
    SegmentKind,
    VariantKind,
)


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


async def _record_event(
    session: Any,
    *,
    workspace_id: str,
    project_id: str,
    asset_id: str,
    action: MediaLifecycleAction,
    asset_version_id: str | None = None,
    actor_user_id: str | None = None,
    canon_revision: int | None = None,
) -> None:
    """Append a lifecycle event inside the transaction making the change."""
    await session.execute(
        text(
            "INSERT INTO media_lifecycle_events (id, workspace_id, project_id, asset_id, "
            "asset_version_id, action, actor_user_id, canon_revision) "
            "VALUES (:id, :w, :p, :a, :v, :action, :actor, :rev)"
        ),
        {
            "id": format_canon_id("operation", str(uuid7())),
            "w": workspace_id,
            "p": project_id,
            "a": asset_id,
            "v": asset_version_id,
            "action": action.value,
            "actor": actor_user_id,
            "rev": canon_revision,
        },
    )


class PostgresMediaStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def list_lifecycle_events(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaLifecycleEvent]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM media_lifecycle_events WHERE workspace_id=:w AND "
                        "project_id=:p AND asset_id=:a ORDER BY seq"
                    ),
                    {"w": workspace_id, "p": project_id, "a": asset_id},
                )
            ).all()
        return [
            MediaLifecycleEvent(
                id=row.id,
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                asset_id=row.asset_id,
                asset_version_id=row.asset_version_id,
                action=MediaLifecycleAction(row.action),
                actor_user_id=row.actor_user_id,
                canon_revision=row.canon_revision,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def create_asset(
        self,
        *,
        workspace_id: str,
        project_id: str,
        name: str,
        actor_user_id: str | None = None,
    ) -> MediaAsset:
        asset_id = format_canon_id("asset", str(uuid7()))
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    "INSERT INTO media_assets (workspace_id, project_id, id, name, lifecycle, "
                    "created_revision) VALUES (:w, :p, :id, :name, 'active', 0)"
                ),
                {"w": workspace_id, "p": project_id, "id": asset_id, "name": name},
            )
            await _record_event(
                session,
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset_id,
                action=MediaLifecycleAction.ASSET_REGISTERED,
                actor_user_id=actor_user_id,
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
        actor_user_id: str | None = None,
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
            await _record_event(
                session,
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset_id,
                asset_version_id=version_id,
                action=MediaLifecycleAction.VERSION_REGISTERED,
                actor_user_id=actor_user_id,
                canon_revision=created_revision,
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

    async def list_versions_for_assets(
        self, *, workspace_id: str, project_id: str, asset_ids: list[str]
    ) -> dict[str, list[MediaAssetVersion]]:
        """Every version of each named asset, oldest first, in one query."""
        if not asset_ids:
            return {}
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM media_asset_versions WHERE workspace_id=:w AND "
                        "project_id=:p AND asset_id = ANY(:ids) ORDER BY asset_id, version"
                    ),
                    {"w": workspace_id, "p": project_id, "ids": list(asset_ids)},
                )
            ).all()
        found: dict[str, list[MediaAssetVersion]] = {}
        for row in rows:
            found.setdefault(row.asset_id, []).append(_asset_version(row))
        return found

    async def discard_asset(self, *, workspace_id: str, project_id: str, asset_id: str) -> None:
        """Remove an asset that never reached the story, with its versions and events.

        Only for an upload whose linking commit failed: nothing in the canon refers
        to it, so it is removed as if it had never been registered.
        """
        scope = {"w": workspace_id, "p": project_id, "a": asset_id}
        async with session_scope(self._session_factory) as session:
            for table, column in (
                ("media_lifecycle_events", "asset_id"),
                ("media_asset_versions", "asset_id"),
                ("media_assets", "id"),
            ):
                await session.execute(
                    text(
                        f"DELETE FROM {table} WHERE workspace_id=:w AND project_id=:p "
                        f"AND {column}=:a"
                    ),
                    scope,
                )

    async def retire_asset(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAsset | None:
        async with session_scope(self._session_factory) as session:
            # Only the first retirement changes state and is recorded; repeating it
            # is a no-op so the audit trail never gains a misleading duplicate.
            result = await session.execute(
                text(
                    "UPDATE media_assets SET lifecycle='retired', retired_revision=:rev "
                    "WHERE workspace_id=:w AND project_id=:p AND id=:id AND lifecycle='active'"
                ),
                {"w": workspace_id, "p": project_id, "id": asset_id, "rev": retired_revision},
            )
            if result.rowcount:  # type: ignore[attr-defined]
                await _record_event(
                    session,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    asset_id=asset_id,
                    action=MediaLifecycleAction.ASSET_RETIRED,
                    actor_user_id=actor_user_id,
                    canon_revision=retired_revision,
                )
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
        actor_user_id: str | None = None,
    ) -> MediaAssetVersion | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE media_asset_versions SET retired_revision=:rev "
                        "WHERE workspace_id=:w AND project_id=:p AND id=:id "
                        "AND retired_revision IS NULL RETURNING asset_id"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "id": version_id,
                        # A retirement always carries a revision so it is not repeatable.
                        "rev": retired_revision if retired_revision is not None else 0,
                    },
                )
            ).first()
            if row is not None:
                await _record_event(
                    session,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    asset_id=row.asset_id,
                    asset_version_id=version_id,
                    action=MediaLifecycleAction.VERSION_RETIRED,
                    actor_user_id=actor_user_id,
                    canon_revision=retired_revision,
                )
        return await self.get_version(
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )

    async def import_asset(self, asset: MediaAsset) -> MediaAsset:
        """Insert an archived asset with its preserved id (idempotent)."""
        async with session_scope(self._session_factory) as session:
            inserted = await session.execute(
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
            if inserted.rowcount:  # type: ignore[attr-defined]
                await _record_event(
                    session,
                    workspace_id=asset.workspace_id,
                    project_id=asset.project_id,
                    asset_id=asset.id,
                    action=MediaLifecycleAction.ASSET_IMPORTED,
                    canon_revision=asset.created_revision,
                )
        created = await self.get_asset(
            workspace_id=asset.workspace_id, project_id=asset.project_id, asset_id=asset.id
        )
        return created or asset

    async def import_version(self, version: MediaAssetVersion) -> MediaAssetVersion:
        """Insert an archived asset version with its preserved id (idempotent)."""
        async with session_scope(self._session_factory) as session:
            inserted = await session.execute(
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
            if inserted.rowcount:  # type: ignore[attr-defined]
                await _record_event(
                    session,
                    workspace_id=version.workspace_id,
                    project_id=version.project_id,
                    asset_id=version.asset_id,
                    asset_version_id=version.id,
                    action=MediaLifecycleAction.VERSION_IMPORTED,
                    canon_revision=version.created_revision,
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


# ------------------------------------------------------------- processing (AST-003)


def _processing(row: Any) -> MediaProcessing:
    return MediaProcessing(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        asset_version_id=row.asset_version_id,
        asset_id=row.asset_id,
        status=ProcessingStatus(row.status),
        processor_version=row.processor_version,
        source_checksum=row.source_checksum,
        attempts=int(row.attempts),
        failure_code=row.failure_code,
        width=row.width,
        height=row.height,
        detected_format=row.detected_format,
        job_id=row.job_id,
        queued_at=row.queued_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        updated_at=row.updated_at,
    )


def _variant(row: Any) -> MediaVariant:
    return MediaVariant(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        asset_version_id=row.asset_version_id,
        kind=VariantKind(row.kind),
        object_key=row.object_key,
        content_type=row.content_type,
        width=int(row.width),
        height=int(row.height),
        byte_length=int(row.byte_length),
        checksum=row.checksum,
        source_checksum=row.source_checksum,
        processor_version=row.processor_version,
        created_at=row.created_at,
    )


def _segment(row: Any) -> MediaSegment:
    return MediaSegment(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        id=row.id,
        asset_id=row.asset_id,
        asset_version_id=row.asset_version_id,
        kind=SegmentKind(row.kind),
        x=int(row.x),
        y=int(row.y),
        width=int(row.width),
        height=int(row.height),
        label=row.label,
        content_hash=row.content_hash,
        created_by=row.created_by,
        processor_version=row.processor_version,
        created_at=row.created_at,
        retired_at=row.retired_at,
    )


@dataclass(frozen=True)
class PendingVersion:
    """A picture version that has no current processing (for the backfill)."""

    workspace_id: str
    project_id: str
    asset_id: str
    asset_version_id: str
    checksum: str
    actor_user_id: str | None


class PostgresMediaProcessingStore:
    """Processing state, smaller copies and segments of picture versions.

    Every statement is scoped by workspace and project except the operator-wide
    summaries (lag, backfill, retention), which return counts and identifiers only.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get_processing(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> MediaProcessing | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM media_processing WHERE workspace_id=:w AND project_id=:p "
                        "AND asset_version_id=:v"
                    ),
                    {"w": workspace_id, "p": project_id, "v": version_id},
                )
            ).first()
        return _processing(row) if row else None

    async def list_processing(
        self, *, workspace_id: str, project_id: str, version_ids: list[str]
    ) -> dict[str, MediaProcessing]:
        if not version_ids:
            return {}
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM media_processing WHERE workspace_id=:w AND project_id=:p "
                        "AND asset_version_id = ANY(:ids)"
                    ),
                    {"w": workspace_id, "p": project_id, "ids": list(version_ids)},
                )
            ).all()
        return {row.asset_version_id: _processing(row) for row in rows}

    async def queue(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        asset_id: str,
        checksum: str,
        processor_version: str,
        job_id: str | None,
    ) -> MediaProcessing:
        """Mark a version as waiting to be processed (again), keeping its attempt count."""
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO media_processing (workspace_id, project_id, "
                        "asset_version_id, asset_id, status, processor_version, source_checksum, "
                        "job_id) VALUES (:w, :p, :v, :a, 'queued', :pv, :sum, :job) "
                        "ON CONFLICT (workspace_id, project_id, asset_version_id) DO UPDATE SET "
                        "status='queued', processor_version=:pv, source_checksum=:sum, "
                        "job_id=:job, failure_code=NULL, queued_at=now(), started_at=NULL, "
                        "finished_at=NULL, updated_at=now() RETURNING *"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "v": version_id,
                        "a": asset_id,
                        "pv": processor_version,
                        "sum": checksum,
                        "job": job_id,
                    },
                )
            ).one()
        return _processing(row)

    async def start(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> MediaProcessing | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE media_processing SET status='processing', attempts=attempts+1, "
                        "started_at=now(), updated_at=now(), failure_code=NULL "
                        "WHERE workspace_id=:w AND project_id=:p AND asset_version_id=:v "
                        "RETURNING *"
                    ),
                    {"w": workspace_id, "p": project_id, "v": version_id},
                )
            ).first()
        return _processing(row) if row else None

    async def fail(
        self, *, workspace_id: str, project_id: str, version_id: str, failure_code: str
    ) -> None:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    "UPDATE media_processing SET status='failed', failure_code=:code, "
                    "finished_at=now(), updated_at=now() WHERE workspace_id=:w AND "
                    "project_id=:p AND asset_version_id=:v"
                ),
                {"w": workspace_id, "p": project_id, "v": version_id, "code": failure_code},
            )

    async def complete(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        width: int,
        height: int,
        detected_format: str,
        variants: list[MediaVariant],
        whole: MediaSegment,
    ) -> list[str]:
        """Record the copies, the whole-picture segment and readiness in one transaction.

        Returns the object keys of copies these replaced, for the caller to delete."""
        scope = {"w": workspace_id, "p": project_id, "v": version_id}
        async with session_scope(self._session_factory) as session:
            old = (
                await session.execute(
                    text(
                        "SELECT object_key FROM media_variants WHERE workspace_id=:w AND "
                        "project_id=:p AND asset_version_id=:v FOR UPDATE"
                    ),
                    scope,
                )
            ).all()
            await session.execute(
                text(
                    "DELETE FROM media_variants WHERE workspace_id=:w AND project_id=:p "
                    "AND asset_version_id=:v"
                ),
                scope,
            )
            for variant in variants:
                await session.execute(
                    text(
                        "INSERT INTO media_variants (workspace_id, project_id, asset_version_id, "
                        "kind, object_key, content_type, width, height, byte_length, checksum, "
                        "source_checksum, processor_version) VALUES (:w, :p, :v, :kind, :key, "
                        ":ct, :width, :height, :bl, :sum, :src, :pv)"
                    ),
                    {
                        **scope,
                        "kind": variant.kind.value,
                        "key": variant.object_key,
                        "ct": variant.content_type,
                        "width": variant.width,
                        "height": variant.height,
                        "bl": variant.byte_length,
                        "sum": variant.checksum,
                        "src": variant.source_checksum,
                        "pv": variant.processor_version,
                    },
                )
            await session.execute(
                text(
                    "INSERT INTO media_segments (workspace_id, project_id, id, asset_id, "
                    "asset_version_id, kind, x, y, width, height, content_hash, created_by, "
                    "processor_version) VALUES (:w, :p, :id, :a, :v, 'whole', 0, 0, :width, "
                    ":height, :hash, NULL, :pv) ON CONFLICT (workspace_id, project_id, "
                    "asset_version_id) WHERE kind = 'whole' DO UPDATE SET width=:width, "
                    "height=:height, content_hash=:hash, processor_version=:pv, retired_at=NULL"
                ),
                {
                    **scope,
                    "id": whole.id,
                    "a": whole.asset_id,
                    "width": whole.width,
                    "height": whole.height,
                    "hash": whole.content_hash,
                    "pv": whole.processor_version,
                },
            )
            await session.execute(
                text(
                    "UPDATE media_processing SET status='ready', failure_code=NULL, width=:width, "
                    "height=:height, detected_format=:fmt, finished_at=now(), updated_at=now() "
                    "WHERE workspace_id=:w AND project_id=:p AND asset_version_id=:v"
                ),
                {**scope, "width": width, "height": height, "fmt": detected_format},
            )
        kept = {variant.object_key for variant in variants}
        return [row.object_key for row in old if row.object_key not in kept]

    async def get_variant(
        self, *, workspace_id: str, project_id: str, version_id: str, kind: VariantKind
    ) -> MediaVariant | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM media_variants WHERE workspace_id=:w AND project_id=:p "
                        "AND asset_version_id=:v AND kind=:kind"
                    ),
                    {"w": workspace_id, "p": project_id, "v": version_id, "kind": kind.value},
                )
            ).first()
        return _variant(row) if row else None

    async def list_variants(
        self, *, workspace_id: str, project_id: str, version_ids: list[str]
    ) -> dict[str, list[MediaVariant]]:
        if not version_ids:
            return {}
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM media_variants WHERE workspace_id=:w AND project_id=:p "
                        "AND asset_version_id = ANY(:ids) ORDER BY asset_version_id, kind"
                    ),
                    {"w": workspace_id, "p": project_id, "ids": list(version_ids)},
                )
            ).all()
        found: dict[str, list[MediaVariant]] = {}
        for row in rows:
            found.setdefault(row.asset_version_id, []).append(_variant(row))
        return found

    async def variant_object_keys(self, *, workspace_id: str, project_id: str) -> set[str]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT object_key FROM media_variants WHERE workspace_id=:w "
                        "AND project_id=:p"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return {row.object_key for row in rows}

    async def known_object_keys(self, *, workspace_id: str, project_id: str) -> set[str]:
        """Every object key a picture version or a smaller copy refers to."""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT object_key FROM media_asset_versions WHERE workspace_id=:w "
                        "AND project_id=:p UNION SELECT object_key FROM media_variants "
                        "WHERE workspace_id=:w AND project_id=:p"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return {row.object_key for row in rows}

    async def remove_variants(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> list[str]:
        """Forget a version's copies and whole-picture segment; the copies' keys to delete."""
        scope = {"w": workspace_id, "p": project_id, "v": version_id}
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "DELETE FROM media_variants WHERE workspace_id=:w AND project_id=:p "
                        "AND asset_version_id=:v RETURNING object_key"
                    ),
                    scope,
                )
            ).all()
            await session.execute(
                text(
                    "UPDATE media_segments SET retired_at=now() WHERE workspace_id=:w AND "
                    "project_id=:p AND asset_version_id=:v AND retired_at IS NULL"
                ),
                scope,
            )
            await session.execute(
                text(
                    "DELETE FROM media_processing WHERE workspace_id=:w AND project_id=:p "
                    "AND asset_version_id=:v"
                ),
                scope,
            )
        return [row.object_key for row in rows]

    async def list_segments(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> list[MediaSegment]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM media_segments WHERE workspace_id=:w AND project_id=:p "
                        "AND asset_version_id=:v AND retired_at IS NULL ORDER BY kind DESC, "
                        "created_at, id"
                    ),
                    {"w": workspace_id, "p": project_id, "v": version_id},
                )
            ).all()
        return [_segment(row) for row in rows]

    async def list_regions(self, *, workspace_id: str, project_id: str) -> list[MediaSegment]:
        """Every current region the author marked in this story (for an archive)."""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM media_segments WHERE workspace_id=:w AND project_id=:p "
                        "AND kind='region' AND retired_at IS NULL ORDER BY created_at, id"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return [_segment(row) for row in rows]

    async def add_segment(self, segment: MediaSegment) -> MediaSegment:
        """Record a region of a picture the author chose."""
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO media_segments (workspace_id, project_id, id, asset_id, "
                        "asset_version_id, kind, x, y, width, height, label, content_hash, "
                        "created_by, processor_version) VALUES (:w, :p, :id, :a, :v, :kind, :x, "
                        ":y, :width, :height, :label, :hash, :by, :pv) ON CONFLICT (workspace_id, "
                        "project_id, id) DO UPDATE SET label=EXCLUDED.label RETURNING *"
                    ),
                    {
                        "w": segment.workspace_id,
                        "p": segment.project_id,
                        "id": segment.id,
                        "a": segment.asset_id,
                        "v": segment.asset_version_id,
                        "kind": segment.kind.value,
                        "x": segment.x,
                        "y": segment.y,
                        "width": segment.width,
                        "height": segment.height,
                        "label": segment.label,
                        "hash": segment.content_hash,
                        "by": segment.created_by,
                        "pv": segment.processor_version,
                    },
                )
            ).one()
        return _segment(row)

    async def retire_segment(
        self, *, workspace_id: str, project_id: str, segment_id: str
    ) -> MediaSegment | None:
        """Stop offering a region the author chose; the whole picture is never retired here."""
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE media_segments SET retired_at=now() WHERE workspace_id=:w AND "
                        "project_id=:p AND id=:id AND kind='region' AND retired_at IS NULL "
                        "RETURNING *"
                    ),
                    {"w": workspace_id, "p": project_id, "id": segment_id},
                )
            ).first()
        return _segment(row) if row else None

    async def workspace_bytes(self, *, workspace_id: str) -> int:
        """Bytes a workspace stores in pictures and their copies (retired ones too: their
        bytes are still kept)."""
        async with session_scope(self._session_factory) as session:
            total = (
                await session.execute(
                    text(
                        "SELECT COALESCE((SELECT sum(byte_length) FROM media_asset_versions "
                        "WHERE workspace_id=:w), 0) + COALESCE((SELECT sum(byte_length) "
                        "FROM media_variants WHERE workspace_id=:w), 0)"
                    ),
                    {"w": workspace_id},
                )
            ).scalar_one()
        return int(total or 0)

    async def lag(self) -> dict[str, Any]:
        """Across every story: how many pictures wait, the oldest wait in seconds, and how
        many failed. Counts only."""
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE status IN ('queued', 'processing')), "
                        "EXTRACT(EPOCH FROM now() - min(queued_at) FILTER (WHERE status IN "
                        "('queued', 'processing'))), count(*) FILTER (WHERE status = 'failed') "
                        "FROM media_processing"
                    )
                )
            ).one()
        waiting = int(row[0] or 0)
        oldest = float(row[1]) if waiting and row[1] is not None else None
        return {
            "waiting": waiting,
            "oldest_seconds": max(0.0, oldest) if oldest is not None else None,
            "failed": int(row[2] or 0),
        }

    async def pending_versions(
        self,
        *,
        processor_version: str,
        content_types: list[str],
        workspace_id: str | None = None,
        project_id: str | None = None,
        limit: int = 500,
        include_failed: bool = False,
        stale_seconds: int = 3600,
    ) -> list[PendingVersion]:
        """Current picture versions never processed, processed by another processor
        version or from other bytes, or left waiting or under way longer than
        ``stale_seconds`` (a worker that died); failed ones too with ``include_failed``.
        Includes the actor who registered each, if known."""
        where = ""
        params: dict[str, Any] = {
            "pv": processor_version,
            "types": list(content_types),
            "limit": limit,
            "failed": include_failed,
            "stale": stale_seconds,
        }
        if workspace_id is not None:
            where += " AND v.workspace_id = :w"
            params["w"] = workspace_id
        if project_id is not None:
            where += " AND v.project_id = :p"
            params["p"] = project_id
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT v.workspace_id, v.project_id, v.asset_id, v.id, v.checksum, "
                        "(SELECT e.actor_user_id FROM media_lifecycle_events e WHERE "
                        "e.workspace_id = v.workspace_id AND e.project_id = v.project_id AND "
                        "e.asset_version_id = v.id AND e.actor_user_id IS NOT NULL "
                        "ORDER BY e.seq LIMIT 1) AS actor_user_id "
                        "FROM media_asset_versions v JOIN media_assets a ON "
                        "a.workspace_id = v.workspace_id AND a.project_id = v.project_id AND "
                        "a.id = v.asset_id LEFT JOIN media_processing m ON "
                        "m.workspace_id = v.workspace_id AND m.project_id = v.project_id AND "
                        "m.asset_version_id = v.id WHERE v.retired_revision IS NULL AND "
                        "a.lifecycle = 'active' AND v.content_type = ANY(:types) AND "
                        "(m.asset_version_id IS NULL OR (m.status IN ('queued', 'processing') "
                        "AND m.updated_at < now() - make_interval(secs => :stale)) OR "
                        "(m.status IN ('ready', 'failed') AND (m.processor_version <> :pv OR "
                        "m.source_checksum <> v.checksum)) OR (:failed AND m.status = 'failed'))"
                        f"{where} "
                        "ORDER BY v.created_at LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return [
            PendingVersion(
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                asset_id=row.asset_id,
                asset_version_id=row.id,
                checksum=row.checksum,
                actor_user_id=row.actor_user_id,
            )
            for row in rows
        ]

    async def retired_with_variants(self, *, limit: int = 1000) -> list[tuple[str, str, str]]:
        """(workspace, project, version) of retired versions, or versions of retired
        pictures, that still have smaller copies."""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT DISTINCT m.workspace_id, m.project_id, m.asset_version_id "
                        "FROM media_variants m JOIN media_asset_versions v ON "
                        "v.workspace_id = m.workspace_id AND v.project_id = m.project_id AND "
                        "v.id = m.asset_version_id JOIN media_assets a ON "
                        "a.workspace_id = v.workspace_id AND a.project_id = v.project_id AND "
                        "a.id = v.asset_id WHERE v.retired_revision IS NOT NULL OR "
                        "a.lifecycle <> 'active' LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ).all()
        return [(row[0], row[1], row[2]) for row in rows]

    async def projects_with_media(self) -> list[tuple[str, str]]:
        """Every (workspace, project) that has any picture record, for reconciliation."""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT workspace_id, project_id FROM media_assets UNION "
                        "SELECT workspace_id, id FROM projects"
                    )
                )
            ).all()
        return sorted({(row[0], row[1]) for row in rows})

    async def health(self) -> bool:
        try:
            async with session_scope(self._session_factory) as session:
                await session.execute(text("SELECT 1 FROM media_processing LIMIT 1"))
            return True
        except Exception:
            return False
