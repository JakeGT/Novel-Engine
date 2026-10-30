"""PostgreSQL store for source texts, their versions, and name memory."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.core.database import session_scope
from backend.domain.errors import ConflictError
from backend.domain.sources import (
    AliasBinding,
    NameExample,
    SourceKind,
    SourceText,
    SourceVersion,
    Verdict,
    VersionOrigin,
    replaces,
)

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
_VERSION_COLUMNS = (
    "workspace_id, project_id, id, source_id, number, content, content_hash, "
    "created_by, created_at, origin, parent_version_id, draft_id, manifest_id"
)
_VERSION = f"SELECT {_VERSION_COLUMNS} FROM source_versions"
_BINDING = (
    "SELECT workspace_id, project_id, id, phrase, normalized, verdict, entity_id, source_id, "
    "decided_by, decided_at, branch_id, from_scene_id, until_scene_id, viewpoint_id, examples "
    "FROM alias_bindings"
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
        origin=VersionOrigin(row[9] or "author"),
        parent_version_id=row[10],
        draft_id=row[11],
        manifest_id=row[12],
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
        branch_id=row[10],
        from_scene_id=row[11],
        until_scene_id=row[12],
        viewpoint_id=row[13],
        examples=_examples(row[14]),
    )


def _examples(raw: Any) -> tuple[NameExample, ...]:
    items = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(items, list):
        return ()
    return tuple(NameExample.from_json(item) for item in items if isinstance(item, dict))


def _examples_json(binding: AliasBinding) -> str:
    return json.dumps([example.as_json() for example in binding.examples])


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
        origin: VersionOrigin = VersionOrigin.AUTHOR,
        parent_version_id: str | None = None,
        draft_id: str | None = None,
        manifest_id: str | None = None,
    ) -> SourceVersion:
        try:
            return await self._insert_version(
                workspace_id=workspace_id,
                project_id=project_id,
                version_id=version_id,
                source_id=source_id,
                content=content,
                content_hash=content_hash,
                actor_user_id=actor_user_id,
                origin=origin,
                parent_version_id=parent_version_id,
                draft_id=draft_id,
                manifest_id=manifest_id,
            )
        except IntegrityError as error:
            # Two saves at the same moment both took the next number; the second loses.
            raise ConflictError(
                "This text changed at the same moment. Your words are still here; try again."
            ) from error

    async def _insert_version(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        source_id: str,
        content: str,
        content_hash: str,
        actor_user_id: str | None,
        origin: VersionOrigin,
        parent_version_id: str | None,
        draft_id: str | None,
        manifest_id: str | None,
    ) -> SourceVersion:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        f"""
                        INSERT INTO source_versions
                            (workspace_id, project_id, id, source_id, number, content,
                             content_hash, created_by, origin, parent_version_id, draft_id,
                             manifest_id)
                        SELECT :ws, :pj, :id, :source, coalesce(max(number), 0) + 1, :content,
                               :hash, :actor, :origin, :parent, :draft, :manifest
                        FROM source_versions
                        WHERE workspace_id = :ws AND project_id = :pj AND source_id = :source
                        RETURNING {_VERSION_COLUMNS}
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
                        "origin": origin.value,
                        "parent": parent_version_id,
                        "draft": draft_id,
                        "manifest": manifest_id,
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

    async def latest_hashes(
        self, *, workspace_id: str, project_id: str, source_ids: list[str]
    ) -> dict[str, str]:
        """The stored checksum of each text's newest version, for texts not removed, in
        one query and without reading any text's words."""
        if not source_ids:
            return {}
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT DISTINCT ON (v.source_id) v.source_id, v.content_hash "
                        "FROM source_versions v JOIN source_texts s ON " + _OWN + " "
                        "WHERE v.workspace_id = :ws AND v.project_id = :pj "
                        "AND v.source_id = ANY(:ids) AND s.removed_at IS NULL "
                        "ORDER BY v.source_id, v.number DESC"
                    ),
                    {"ws": workspace_id, "pj": project_id, "ids": list(source_ids)},
                )
            ).all()
        return {str(row[0]): str(row[1]) for row in rows}

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
            params = {
                "ws": binding.workspace_id,
                "pj": binding.project_id,
                "normalized": binding.normalized,
            }
            # Serialise answers about one phrase, so two at once cannot both survive.
            await db.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtext(:ws || ':' || :pj || ':' || :normalized))"
                ),
                params,
            )
            rows = (
                await db.execute(
                    text(
                        _BINDING + " WHERE workspace_id = :ws AND project_id = :pj "
                        "AND normalized = :normalized"
                    ),
                    params,
                )
            ).all()
            doomed = [item.id for item in map(_binding, rows) if replaces(item, binding)]
            if doomed:
                await db.execute(
                    text(
                        "DELETE FROM alias_bindings WHERE workspace_id = :ws "
                        "AND project_id = :pj AND id = ANY(:ids)"
                    ),
                    {**params, "ids": doomed},
                )
            await db.execute(
                text(
                    "INSERT INTO alias_bindings (workspace_id, project_id, id, phrase, "
                    "normalized, verdict, entity_id, source_id, decided_by, branch_id, "
                    "from_scene_id, until_scene_id, viewpoint_id, examples) VALUES "
                    "(:ws, :pj, :id, :phrase, :normalized, :verdict, :entity, :source, :actor, "
                    ":branch, :from_scene, :until_scene, :viewpoint, CAST(:examples AS JSONB))"
                ),
                {
                    **params,
                    "id": binding.id,
                    "phrase": binding.phrase,
                    "verdict": binding.verdict.value,
                    "entity": binding.entity_id,
                    "source": binding.source_id,
                    "actor": binding.decided_by,
                    "branch": binding.branch_id,
                    "from_scene": binding.from_scene_id,
                    "until_scene": binding.until_scene_id,
                    "viewpoint": binding.viewpoint_id,
                    "examples": _examples_json(binding),
                },
            )

    async def set_examples(self, binding: AliasBinding) -> bool:
        """Keep a new list of examples on an answer; False when the answer is gone."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "UPDATE alias_bindings SET examples = CAST(:examples AS JSONB) "
                    "WHERE workspace_id = :ws AND project_id = :pj AND id = :id"
                ),
                {
                    "ws": binding.workspace_id,
                    "pj": binding.project_id,
                    "id": binding.id,
                    "examples": _examples_json(binding),
                },
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

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
