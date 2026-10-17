"""PostgreSQL stores for the passage index and context manifests (ADR-021)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.context import SavedManifest
from backend.domain.passages import IndexedPassage


def _passage(row: Any) -> IndexedPassage:
    ids = row.entity_ids if isinstance(row.entity_ids, list) else json.loads(row.entity_ids or "[]")
    return IndexedPassage(
        source_id=row.source_id,
        version_id=row.version_id,
        number=int(row.number),
        start=int(row.start_char),
        end=int(row.end_char),
        section=row.section,
        entity_ids=tuple(ids),
        indexed_revision=int(row.indexed_revision),
    )


class PostgresPassageStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def replace_source(
        self,
        *,
        workspace_id: str,
        project_id: str,
        source_id: str,
        passages: Sequence[IndexedPassage],
    ) -> None:
        """A text's passages now: whatever was indexed for it before is replaced."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "DELETE FROM text_passages "
                    "WHERE workspace_id = :w AND project_id = :p AND source_id = :s"
                ),
                {"w": workspace_id, "p": project_id, "s": source_id},
            )
            for passage in passages:
                await db.execute(
                    text(
                        "INSERT INTO text_passages (workspace_id, project_id, source_id, "
                        "version_id, number, start_char, end_char, section, entity_ids, "
                        "indexed_revision) VALUES (:w, :p, :s, :v, :n, :a, :b, :section, "
                        "CAST(:ids AS JSONB), :rev)"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "s": source_id,
                        "v": passage.version_id,
                        "n": passage.number,
                        "a": passage.start,
                        "b": passage.end,
                        "section": passage.section[:200],
                        "ids": json.dumps(list(passage.entity_ids)),
                        "rev": passage.indexed_revision,
                    },
                )

    async def forget_source(self, *, workspace_id: str, project_id: str, source_id: str) -> None:
        await self.replace_source(
            workspace_id=workspace_id, project_id=project_id, source_id=source_id, passages=[]
        )

    async def for_source(
        self, *, workspace_id: str, project_id: str, source_id: str
    ) -> list[IndexedPassage]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT * FROM text_passages WHERE workspace_id = :w AND project_id = :p "
                        "AND source_id = :s ORDER BY number"
                    ),
                    {"w": workspace_id, "p": project_id, "s": source_id},
                )
            ).all()
        return [_passage(row) for row in rows]

    async def mentioning(
        self, *, workspace_id: str, project_id: str, entity_ids: Sequence[str], limit: int = 200
    ) -> list[IndexedPassage]:
        if not entity_ids:
            return []
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT * FROM text_passages WHERE workspace_id = :w AND project_id = :p "
                        "AND entity_ids ?| CAST(:ids AS TEXT[]) "
                        "ORDER BY source_id, number LIMIT :limit"
                    ),
                    {"w": workspace_id, "p": project_id, "ids": list(entity_ids), "limit": limit},
                )
            ).all()
        return [_passage(row) for row in rows]

    async def indexed_versions(
        self, *, workspace_id: str, project_id: str
    ) -> dict[str, tuple[str, int]]:
        """Source id → (the version indexed, the story's version when it was)."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT source_id, min(version_id) AS version_id, "
                        "min(indexed_revision) AS revision FROM text_passages "
                        "WHERE workspace_id = :w AND project_id = :p GROUP BY source_id"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return {row.source_id: (row.version_id, int(row.revision)) for row in rows}


class PostgresManifestStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def save(
        self,
        *,
        workspace_id: str,
        project_id: str,
        manifest_id: str,
        branch_id: str | None,
        purpose: str,
        plan_id: str | None,
        policy_version: str,
        checksum: str,
        words: int,
        budget: int,
        manifest: dict[str, Any],
        created_by: str | None,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO context_manifests (workspace_id, project_id, id, branch_id, "
                    "purpose, plan_id, policy_version, checksum, words, budget, manifest, "
                    "created_by) VALUES (:w, :p, :id, :b, :purpose, :plan, :policy, :checksum, "
                    ":words, :budget, CAST(:manifest AS JSONB), :by)"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "id": manifest_id,
                    "b": branch_id,
                    "purpose": purpose,
                    "plan": plan_id,
                    "policy": policy_version,
                    "checksum": checksum,
                    "words": words,
                    "budget": budget,
                    "manifest": json.dumps(manifest),
                    "by": created_by,
                },
            )

    async def get(
        self, *, workspace_id: str, project_id: str, manifest_id: str
    ) -> SavedManifest | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT * FROM context_manifests "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": manifest_id},
                )
            ).first()
        if row is None:
            return None
        manifest = row.manifest if isinstance(row.manifest, dict) else json.loads(row.manifest)
        return SavedManifest(
            id=row.id,
            purpose=row.purpose,
            plan_id=row.plan_id,
            policy_version=row.policy_version,
            checksum=row.checksum,
            words=int(row.words),
            budget=int(row.budget),
            manifest=manifest,
            created_by=row.created_by,
            created_at=row.created_at,
        )


__all__ = ["PostgresManifestStore", "PostgresPassageStore"]
