"""PostgreSQL store for source texts, their versions, and name memory."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.sources import AliasBinding, SourceKind, SourceText, SourceVersion, Verdict

_OWN = "v.workspace_id = s.workspace_id AND v.project_id = s.project_id AND v.source_id = s.id"
_SOURCE = f"""
    SELECT s.workspace_id, s.project_id, s.id, s.title, s.kind, s.created_by, s.created_at,
           s.removed_at,
           (SELECT count(*) FROM source_versions v WHERE {_OWN}),
           (SELECT v.id FROM source_versions v WHERE {_OWN} ORDER BY v.number DESC LIMIT 1),
           (SELECT length(v.content) FROM source_versions v WHERE {_OWN}
             ORDER BY v.number DESC LIMIT 1)
    FROM source_texts s
"""
_VERSION = (
    "SELECT workspace_id, project_id, id, source_id, number, content, content_hash, "
    "created_by, created_at FROM source_versions"
)
_BINDING = (
    "SELECT workspace_id, project_id, id, phrase, normalized, verdict, entity_id, source_id, "
    "decided_by, decided_at FROM alias_bindings"
)


def _source(row: Any) -> SourceText:
    return SourceText(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        title=row[3],
        kind=SourceKind(row[4]),
        created_by=row[5],
        created_at=row[6],
        removed_at=row[7],
        versions=int(row[8] or 0),
        latest_version_id=row[9],
        length=int(row[10] or 0),
    )


def _version(row: Any) -> SourceVersion:
    return SourceVersion(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        source_id=row[3],
        number=row[4],
        content=row[5],
        content_hash=row[6],
        created_by=row[7],
        created_at=row[8],
    )


def _binding(row: Any) -> AliasBinding:
    return AliasBinding(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        phrase=row[3],
        normalized=row[4],
        verdict=Verdict(row[5]),
        entity_id=row[6],
        source_id=row[7],
        decided_by=row[8],
        decided_at=row[9],
    )


class PostgresSourceStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------- sources
    async def add_source(
        self,
        *,
        workspace_id: str,
        project_id: str,
        source_id: str,
        title: str,
        kind: SourceKind,
        actor_user_id: str | None,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO source_texts (workspace_id, project_id, id, title, kind, "
                    "created_by) VALUES (:ws, :pj, :id, :title, :kind, :actor)"
                ),
                {
                    "ws": workspace_id,
                    "pj": project_id,
                    "id": source_id,
                    "title": title,
                    "kind": kind.value,
                    "actor": actor_user_id,
                },
            )

    async def add_version(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        source_id: str,
        content: str,
        content_hash: str,
        actor_user_id: str | None,
    ) -> SourceVersion:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        """
                        INSERT INTO source_versions
                            (workspace_id, project_id, id, source_id, number, content,
                             content_hash, created_by)
                        SELECT :ws, :pj, :id, :source, coalesce(max(number), 0) + 1, :content,
                               :hash, :actor
                        FROM source_versions
                        WHERE workspace_id = :ws AND project_id = :pj AND source_id = :source
                        RETURNING workspace_id, project_id, id, source_id, number, content,
                                  content_hash, created_by, created_at
                        """
                    ),
                    {
                        "ws": workspace_id,
                        "pj": project_id,
                        "id": version_id,
                        "source": source_id,
                        "content": content,
                        "hash": content_hash,
                        "actor": actor_user_id,
                    },
                )
            ).one()
        return _version(row)

    async def rename(
        self, *, workspace_id: str, project_id: str, source_id: str, title: str
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE source_texts SET title = :title WHERE workspace_id = :ws "
                    "AND project_id = :pj AND id = :id"
                ),
                {"ws": workspace_id, "pj": project_id, "id": source_id, "title": title},
            )

    async def list_sources(self, *, workspace_id: str, project_id: str) -> list[SourceText]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _SOURCE + " WHERE s.workspace_id = :ws AND s.project_id = :pj "
                        "AND s.removed_at IS NULL ORDER BY s.created_at DESC, s.id DESC"
                    ),
                    {"ws": workspace_id, "pj": project_id},
                )
            ).all()
        return [_source(row) for row in rows]

    async def get_source(
        self, *, workspace_id: str, project_id: str, source_id: str
    ) -> SourceText | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        _SOURCE + " WHERE s.workspace_id = :ws AND s.project_id = :pj "
                        "AND s.id = :id"
                    ),
                    {"ws": workspace_id, "pj": project_id, "id": source_id},
                )
            ).first()
        return _source(row) if row is not None else None

    async def list_versions(
        self, *, workspace_id: str, project_id: str, source_id: str
    ) -> list[SourceVersion]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _VERSION + " WHERE workspace_id = :ws AND project_id = :pj "
                        "AND source_id = :id ORDER BY number"
                    ),
                    {"ws": workspace_id, "pj": project_id, "id": source_id},
                )
            ).all()
        return [_version(row) for row in rows]

    async def get_version(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> SourceVersion | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(_VERSION + " WHERE workspace_id = :ws AND project_id = :pj AND id = :id"),
                    {"ws": workspace_id, "pj": project_id, "id": version_id},
                )
            ).first()
        return _version(row) if row is not None else None

    async def remove_source(self, *, workspace_id: str, project_id: str, source_id: str) -> None:
        """Erase the words, keep a title-only marker so old evidence can say where it led."""
        async with session_scope(self._session_factory) as db:
            params = {"ws": workspace_id, "pj": project_id, "id": source_id}
            await db.execute(
                text(
                    "DELETE FROM source_versions WHERE workspace_id = :ws AND project_id = :pj "
                    "AND source_id = :id"
                ),
                params,
            )
            await db.execute(
                text(
                    "DELETE FROM alias_bindings WHERE workspace_id = :ws AND project_id = :pj "
                    "AND source_id = :id"
                ),
                params,
            )
            await db.execute(
                text(
                    "UPDATE source_texts SET removed_at = now() WHERE workspace_id = :ws "
                    "AND project_id = :pj AND id = :id"
                ),
                params,
            )

    # ------------------------------------------------------------ bindings
    async def list_bindings(self, *, workspace_id: str, project_id: str) -> list[AliasBinding]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _BINDING + " WHERE workspace_id = :ws AND project_id = :pj "
                        "ORDER BY decided_at, id"
                    ),
                    {"ws": workspace_id, "pj": project_id},
                )
            ).all()
        return [_binding(row) for row in rows]

    async def put_binding(self, binding: AliasBinding) -> None:
        """One decision per phrase, entry and scope: a later answer replaces an earlier one."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    DELETE FROM alias_bindings
                    WHERE workspace_id = :ws AND project_id = :pj AND normalized = :normalized
                      AND source_id IS NOT DISTINCT FROM :source
                      AND (entity_id IS NOT DISTINCT FROM :entity
                           OR verdict = 'ignore' OR :verdict = 'ignore'
                           OR (:verdict = 'is' AND verdict IN ('is', 'suggested')))
                    """
                ),
                {
                    "ws": binding.workspace_id,
                    "pj": binding.project_id,
                    "normalized": binding.normalized,
                    "source": binding.source_id,
                    "entity": binding.entity_id,
                    "verdict": binding.verdict.value,
                },
            )
            await db.execute(
                text(
                    "INSERT INTO alias_bindings (workspace_id, project_id, id, phrase, "
                    "normalized, verdict, entity_id, source_id, decided_by) VALUES "
                    "(:ws, :pj, :id, :phrase, :normalized, :verdict, :entity, :source, :actor)"
                ),
                {
                    "ws": binding.workspace_id,
                    "pj": binding.project_id,
                    "id": binding.id,
                    "phrase": binding.phrase,
                    "normalized": binding.normalized,
                    "verdict": binding.verdict.value,
                    "entity": binding.entity_id,
                    "source": binding.source_id,
                    "actor": binding.decided_by,
                },
            )

    async def delete_binding(self, *, workspace_id: str, project_id: str, binding_id: str) -> bool:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM alias_bindings WHERE workspace_id = :ws AND project_id = :pj "
                    "AND id = :id"
                ),
                {"ws": workspace_id, "pj": project_id, "id": binding_id},
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    # --------------------------------------------------------------- purge
    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            params = {"ws": workspace_id, "pj": project_id}
            await db.execute(
                text("DELETE FROM alias_bindings WHERE workspace_id = :ws AND project_id = :pj"),
                params,
            )
            await db.execute(
                text("DELETE FROM source_versions WHERE workspace_id = :ws AND project_id = :pj"),
                params,
            )
            result = await db.execute(
                text("DELETE FROM source_texts WHERE workspace_id = :ws AND project_id = :pj"),
                params,
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def delete_workspace(self, workspace_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text("DELETE FROM alias_bindings WHERE workspace_id = :ws"), {"ws": workspace_id}
            )
            await db.execute(
                text("DELETE FROM source_versions WHERE workspace_id = :ws"), {"ws": workspace_id}
            )
            result = await db.execute(
                text("DELETE FROM source_texts WHERE workspace_id = :ws"), {"ws": workspace_id}
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
