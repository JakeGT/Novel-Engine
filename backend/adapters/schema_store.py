"""PostgreSQL-backed project schema-version store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.adapters.system import uuid7
from backend.core.database import session_scope
from backend.domain.canon.ids import format_canon_id
from backend.domain.schemas import (
    SchemaVersion,
    SchemaVersionStatus,
)


def _row_to_version(row: Any) -> SchemaVersion:
    return SchemaVersion(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        version=row.version,
        status=SchemaVersionStatus(row.status),
        definition=dict(row.definition),
        content_hash=row.content_hash,
        compatibility=row.compatibility,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        published_at=row.published_at,
    )


class PostgresSchemaVersionStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def create_draft(
        self,
        *,
        workspace_id: str,
        project_id: str,
        definition: dict[str, Any],
        content_hash: str,
        compatibility: str,
        created_by_user_id: str | None,
    ) -> SchemaVersion:
        version_id = format_canon_id("schema_version", str(uuid7()))
        async with session_scope(self._session_factory) as session:
            next_version = (
                await session.execute(
                    text(
                        "SELECT COALESCE(MAX(version), 0) + 1 FROM schema_versions "
                        "WHERE workspace_id = :w AND project_id = :p"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).scalar_one()
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO schema_versions (
                            id, workspace_id, project_id, version, status,
                            definition, content_hash, compatibility, created_by_user_id
                        )
                        VALUES (
                            :id, :w, :p, :version, 'draft',
                            CAST(:definition AS JSONB), :hash, :compat, :creator
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": version_id,
                        "w": workspace_id,
                        "p": project_id,
                        "version": next_version,
                        "definition": json.dumps(definition),
                        "hash": content_hash,
                        "compat": compatibility,
                        "creator": created_by_user_id,
                    },
                )
            ).one()
        return _row_to_version(row)

    async def get(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> SchemaVersion | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM schema_versions "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": version_id},
                )
            ).first()
        return _row_to_version(row) if row else None

    async def get_published(self, *, workspace_id: str, project_id: str) -> SchemaVersion | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM schema_versions "
                        "WHERE workspace_id = :w AND project_id = :p AND status = 'published' "
                        "ORDER BY version DESC LIMIT 1"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).first()
        return _row_to_version(row) if row else None

    async def list(self, *, workspace_id: str, project_id: str) -> list[SchemaVersion]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM schema_versions "
                        "WHERE workspace_id = :w AND project_id = :p ORDER BY version DESC"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return [_row_to_version(row) for row in rows]

    async def publish(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> SchemaVersion | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE schema_versions SET status = 'published', published_at = :now "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id "
                        "AND status = 'draft' RETURNING *"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "id": version_id,
                        "now": datetime.now(UTC),
                    },
                )
            ).first()
        return _row_to_version(row) if row else None
