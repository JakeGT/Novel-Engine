"""Generation-versioned vectors in PostgreSQL/pgvector (CTX-012, migration 0047).

One table holds every generation's vectors in an untyped ``vector`` column. Each
generation has its own partial HNSW index on ``(embedding::vector(N))``, and every query
names one generation and casts to its size, so the planner can use that index and two
spaces are never compared. A trigger refuses rows of the wrong size or into a retired
generation, beneath the checks here.

Gates (project, current picture and version, not rejected, segment not retired, and the
point-of-view secrecy rules) sit in the same WHERE clause as the similarity ordering:
an ineligible row is never ranked. Iterative index scans (pgvector 0.8) are switched on
per query so a filtered search still fills its limit.

Never logs the words, bytes or vectors it stores.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.types.json import Jsonb

from backend.adapters.system import uuid7
from backend.core.logging import instrument
from backend.domain.embedding import (
    CaptionSource,
    EmbeddingError,
    EmbeddingSpace,
    EmbeddingTask,
    Generation,
    GenerationState,
    MixedSpaceError,
    Modality,
    PictureSource,
    SearchGate,
    StoredHit,
    VectorRow,
    require_generation_id,
)

logger = logging.getLogger(__name__)

_GENERATION_COLUMNS = (
    "id, model, revision, dimension, native_dimension, modalities, tasks, task_set, "
    "normalization, space_key, state, previous_generation_id, cancel_requested, note, "
    "created_at, activated_at, retired_at"
)


def _generation(row: Sequence[Any]) -> Generation:
    return Generation(
        id=row[0],
        model=row[1],
        revision=row[2],
        dimension=int(row[3]),
        native_dimension=int(row[4]),
        modalities=frozenset(Modality(value) for value in row[5]),
        tasks=dict(row[6] or {}),
        task_set=row[7],
        normalization=row[8],
        space_key=row[9],
        state=GenerationState(row[10]),
        previous_generation_id=row[11],
        cancel_requested=bool(row[12]),
        note=row[13],
        created_at=row[14].isoformat() if row[14] else None,
        activated_at=row[15].isoformat() if row[15] else None,
        retired_at=row[16].isoformat() if row[16] else None,
    )


# The first key of the two-key advisory locks taken by ``hold_lock``, apart from the
# journal's (12_012) and the story views' (12_013).
_LOCK_SPACE = 12_014


def _index_name(generation_id: str) -> sql.Identifier:
    return sql.Identifier(f"embedding_vectors_hnsw_{require_generation_id(generation_id)}")


def _gate_sql(gate: SearchGate, *, asset: str, version: str | None) -> str:
    """Eligibility of a picture, as SQL over ``%(w)s``/``%(p)s`` and the given columns.

    With ``version`` None the picture's newest current version is the one judged."""
    version_match = f"mv.id = {version}" if version else "true"
    clauses = [
        "EXISTS (SELECT 1 FROM media_assets a WHERE a.workspace_id = %(w)s "
        f"AND a.project_id = %(p)s AND a.id = {asset} AND a.lifecycle = 'active')",
        "EXISTS (SELECT 1 FROM media_asset_versions mv WHERE mv.workspace_id = %(w)s "
        f"AND mv.project_id = %(p)s AND mv.asset_id = {asset} AND {version_match} "
        "AND mv.retired_revision IS NULL AND COALESCE(mv.authority, '') <> 'rejected' "
        + ("AND COALESCE(mv.visibility, '') <> 'secret' " if gate.point_of_view else "")
        + "AND NOT EXISTS (SELECT 1 FROM media_asset_versions newer "
        "WHERE newer.workspace_id = mv.workspace_id AND newer.project_id = mv.project_id "
        "AND newer.asset_id = mv.asset_id AND newer.version > mv.version "
        "AND newer.retired_revision IS NULL))",
    ]
    if gate.point_of_view:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM asset_links l WHERE l.workspace_id = %(w)s "
            f"AND l.project_id = %(p)s AND l.asset_id = {asset} "
            "AND l.retired_revision IS NULL AND l.visibility = 'secret' "
            "AND l.branch_id = COALESCE(CAST(%(wb)s AS TEXT), (SELECT b.id FROM branches b "
            "WHERE b.workspace_id = %(w)s AND b.project_id = %(p)s AND b.is_root)) "
            # A secret link reaches a point of view only when its character is among
            # those who know (AST-004); with no character, never.
            "AND NOT (l.known_by @> jsonb_build_array(CAST(%(pov)s AS TEXT))))"
        )
    return " AND ".join(clauses)


def _gate_params(gate: SearchGate) -> dict[str, Any]:
    return {"wb": gate.branch_id, "pov": gate.point_of_view_id}


class PgEmbeddingIndexStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self._dsn) as connection:
            register_vector(connection)
            yield connection

    # --- generations ---------------------------------------------------------------

    def _one(self, connection: psycopg.Connection, where: str, *args: Any) -> Generation | None:
        row = connection.execute(
            f"SELECT {_GENERATION_COLUMNS} FROM embedding_generations WHERE {where}", args
        ).fetchone()
        return _generation(row) if row else None

    def active(self) -> Generation | None:
        with self._connect() as connection:
            return self._one(connection, "state = 'active'")

    def building(self) -> Generation | None:
        with self._connect() as connection:
            return self._one(connection, "state = 'building'")

    def get(self, generation_id: str) -> Generation | None:
        with self._connect() as connection:
            return self._one(connection, "id = %s", generation_id)

    def list_generations(self) -> list[Generation]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {_GENERATION_COLUMNS} FROM embedding_generations ORDER BY created_at, id"
            ).fetchall()
        return [_generation(row) for row in rows]

    @instrument("embedding.generation.create", logger)
    def create_building(self, space: EmbeddingSpace, *, note: str | None = None) -> Generation:
        generation_id = require_generation_id(f"gen_{uuid7().hex[-20:]}")
        with self._connect() as connection:
            if self._one(connection, "state = 'building'") is not None:
                raise EmbeddingError("a new search index is already being built")
            active = self._one(connection, "state = 'active'")
            connection.execute(
                "INSERT INTO embedding_generations (id, model, revision, dimension, "
                "native_dimension, modalities, tasks, task_set, normalization, space_key, "
                "state, previous_generation_id, note) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'building', %s, %s)",
                (
                    generation_id,
                    space.model,
                    space.revision,
                    space.dimension,
                    space.native_dimension,
                    sorted(m.value for m in space.modalities),
                    Jsonb(space.tasks),
                    space.task_set,
                    space.normalization,
                    space.key,
                    active.id if active else None,
                    note,
                ),
            )
            connection.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {} ON embedding_vectors USING hnsw "
                    "((embedding::vector({})) vector_cosine_ops) WHERE generation_id = {}"
                ).format(
                    _index_name(generation_id),
                    sql.Literal(space.dimension),
                    sql.Literal(generation_id),
                )
            )
            created = self._one(connection, "id = %s", generation_id)
        assert created is not None
        return created

    def request_cancel(self, generation_id: str) -> bool:
        with self._connect() as connection:
            return bool(
                connection.execute(
                    "UPDATE embedding_generations SET cancel_requested = true "
                    "WHERE id = %s AND state = 'building'",
                    (generation_id,),
                ).rowcount
            )

    @instrument("embedding.generation.activate", logger)
    def activate(self, generation_id: str) -> Generation:
        """Make a generation the one searched, in one transaction.

        The generation in use is retired (its rows are kept for a rollback), and the
        new one remembers it as its previous generation."""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {_GENERATION_COLUMNS} FROM embedding_generations "
                "WHERE state = 'active' OR id = %s FOR UPDATE",
                (generation_id,),
            ).fetchall()
            found = {row[0]: _generation(row) for row in rows}
            target = found.get(generation_id)
            if target is None:
                raise EmbeddingError("that search index does not exist")
            if target.state is GenerationState.ACTIVE:
                return target
            current = next((g for g in found.values() if g.state is GenerationState.ACTIVE), None)
            if current is not None:
                connection.execute(
                    "UPDATE embedding_generations SET state = 'retired', retired_at = now() "
                    "WHERE id = %s",
                    (current.id,),
                )
            connection.execute(
                "UPDATE embedding_generations SET state = 'active', activated_at = now(), "
                "retired_at = NULL, cancel_requested = false, "
                "previous_generation_id = COALESCE(%s, previous_generation_id) WHERE id = %s",
                (current.id if current else None, generation_id),
            )
            activated = self._one(connection, "id = %s", generation_id)
        assert activated is not None
        return activated

    def rollback(self) -> Generation:
        active = self.active()
        if active is None or not active.previous_generation_id:
            raise EmbeddingError("there is no earlier search index to go back to")
        previous = self.get(active.previous_generation_id)
        if previous is None:
            raise EmbeddingError("the earlier search index has been removed")
        return self.activate(previous.id)

    @instrument("embedding.generation.discard", logger)
    def discard(self, generation_id: str) -> int:
        with self._connect() as connection:
            generation = self._one(connection, "id = %s FOR UPDATE", generation_id)
            if generation is None:
                return 0
            if generation.state is GenerationState.ACTIVE:
                raise EmbeddingError("the search index in use cannot be removed")
            count = connection.execute(
                "SELECT count(*) FROM embedding_vectors WHERE generation_id = %s",
                (generation_id,),
            ).fetchone()
            connection.execute(
                sql.SQL("DROP INDEX IF EXISTS {}").format(_index_name(generation_id))
            )
            connection.execute("DELETE FROM embedding_generations WHERE id = %s", (generation_id,))
        return int(count[0]) if count else 0

    # --- rows ----------------------------------------------------------------------

    @instrument("embedding.vectors.upsert", logger)
    def upsert(
        self, generation_id: str, rows: Sequence[VectorRow], *, workspace_id: str, project_id: str
    ) -> int:
        if not rows:
            return 0
        with self._connect() as connection:
            generation = self._one(connection, "id = %s", generation_id)
            if generation is None:
                raise EmbeddingError("that search index does not exist")
            if generation.state is GenerationState.RETIRED:
                raise EmbeddingError("a retired search index takes no new rows")
            for row in rows:
                if len(row.values) != generation.dimension:
                    raise MixedSpaceError(
                        f"a {len(row.values)}-dimension vector cannot go into generation "
                        f"{generation.id}, which holds {generation.dimension}"
                    )
                if row.modality not in generation.modalities:
                    raise MixedSpaceError(
                        f"generation {generation.id} does not hold {row.modality.value}"
                    )
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO embedding_vectors (generation_id, workspace_id, project_id, id, "
                    "kind, modality, task, document, metadata, asset_id, asset_version_id, "
                    "segment_id, content_hash, embedding) VALUES "
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (generation_id, workspace_id, project_id, id) DO UPDATE SET "
                    "kind = EXCLUDED.kind, modality = EXCLUDED.modality, task = EXCLUDED.task, "
                    "document = EXCLUDED.document, metadata = EXCLUDED.metadata, "
                    "asset_id = EXCLUDED.asset_id, asset_version_id = EXCLUDED.asset_version_id, "
                    "segment_id = EXCLUDED.segment_id, content_hash = EXCLUDED.content_hash, "
                    "embedding = EXCLUDED.embedding, created_at = now()",
                    [
                        (
                            generation_id,
                            workspace_id,
                            project_id,
                            row.id,
                            row.kind,
                            row.modality.value,
                            row.task.value,
                            row.document,
                            Jsonb(row.metadata),
                            row.asset_id,
                            row.asset_version_id,
                            row.segment_id,
                            row.content_hash,
                            Vector(list(row.values)),
                        )
                        for row in rows
                    ],
                )
        return len(rows)

    def hashes(self, generation_id: str, *, workspace_id: str, project_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, content_hash FROM embedding_vectors WHERE generation_id = %s "
                "AND workspace_id = %s AND project_id = %s",
                (generation_id, workspace_id, project_id),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def text_rows(
        self, generation_id: str, *, workspace_id: str, project_id: str
    ) -> list[VectorRow]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, kind, task, content_hash, document, metadata, asset_id, "
                "asset_version_id, segment_id FROM embedding_vectors WHERE generation_id = %s "
                "AND workspace_id = %s AND project_id = %s AND modality = 'text' "
                "AND document IS NOT NULL ORDER BY id",
                (generation_id, workspace_id, project_id),
            ).fetchall()
        return [
            VectorRow(
                id=row[0],
                kind=row[1],
                modality=Modality.TEXT,
                task=EmbeddingTask(row[2]) if row[2] in EmbeddingTask else EmbeddingTask.DOCUMENT,
                content_hash=row[3],
                values=(),
                document=row[4],
                metadata=dict(row[5] or {}),
                asset_id=row[6],
                asset_version_id=row[7],
                segment_id=row[8],
            )
            for row in rows
        ]

    @instrument("embedding.vectors.search", logger)
    def search(
        self,
        generation_id: str,
        values: Sequence[float],
        *,
        workspace_id: str,
        project_id: str,
        gate: SearchGate,
        limit: int,
    ) -> list[StoredHit]:
        with self._connect() as connection:
            generation = self._one(connection, "id = %s", generation_id)
            if generation is None:
                raise EmbeddingError("that search index does not exist")
            if len(values) != generation.dimension:
                raise MixedSpaceError(
                    f"a {len(values)}-dimension query cannot search generation "
                    f"{generation.id}, which holds {generation.dimension}"
                )
            # Filters are applied during the index scan; without this a filtered HNSW
            # search can return fewer rows than asked for (pgvector README, 0.8.0).
            connection.execute("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
            distance = sql.SQL("v.embedding::vector({dim}) <=> %(q)s::vector({dim})").format(
                dim=sql.Literal(generation.dimension)
            )
            filters = ["AND v.kind = ANY(%(kinds)s)"] if gate.kinds else []
            if gate.assets is True:
                filters.append("AND v.asset_id IS NOT NULL")
            elif gate.assets is False:
                filters.append("AND v.asset_id IS NULL")
            # Metadata filters (CTX-004): the key and its values travel as parameters.
            meta: dict[str, Any] = {}
            for number, (key, allowed) in enumerate(gate.metadata):
                filters.append(f"AND v.metadata->>%(mk{number})s = ANY(%(mv{number})s)")
                meta[f"mk{number}"] = key
                meta[f"mv{number}"] = list(allowed)
            # Fixed fragments only.
            kinds = sql.SQL(" ".join(filters))  # type: ignore[arg-type]
            query = sql.SQL(
                "SELECT v.id, v.kind, v.modality, v.document, v.metadata, v.asset_id, "
                "v.asset_version_id, v.segment_id, 1 - ({distance}) AS score "
                "FROM embedding_vectors v WHERE v.generation_id = {generation} "
                "AND v.workspace_id = %(w)s AND v.project_id = %(p)s {kinds} "
                "AND (v.asset_id IS NULL OR ({gate})) "
                "AND (v.segment_id IS NULL OR NOT EXISTS (SELECT 1 FROM media_segments s "
                "WHERE s.workspace_id = %(w)s AND s.project_id = %(p)s AND s.id = v.segment_id "
                "AND s.retired_at IS NOT NULL)) "
                "ORDER BY {distance}, v.id LIMIT %(limit)s"
            ).format(
                distance=distance,
                generation=sql.Literal(generation.id),
                kinds=kinds,
                # Assembled from fixed fragments only; values travel as parameters.
                gate=sql.SQL(_gate_sql(gate, asset="v.asset_id", version="v.asset_version_id")),  # type: ignore[arg-type]
            )
            rows = connection.execute(
                query,
                {
                    "q": Vector(list(values)),
                    "w": workspace_id,
                    "p": project_id,
                    "kinds": sorted(gate.kinds or ()),
                    "limit": limit,
                    **_gate_params(gate),
                    **meta,
                },
            ).fetchall()
        return [
            StoredHit(
                id=row[0],
                kind=row[1],
                modality=Modality(row[2]),
                document=row[3],
                metadata=dict(row[4] or {}),
                asset_id=row[5],
                asset_version_id=row[6],
                segment_id=row[7],
                score=float(row[8]),
                generation_id=generation.id,
            )
            for row in rows
        ]

    def eligible_assets(
        self, asset_ids: Sequence[str], *, workspace_id: str, project_id: str, gate: SearchGate
    ) -> set[str]:
        if not asset_ids:
            return set()
        query = "SELECT x.id FROM unnest(CAST(%(ids)s AS TEXT[])) AS x(id) WHERE " + _gate_sql(
            gate, asset="x.id", version=None
        )
        with self._connect() as connection:
            rows = connection.execute(
                query,
                {"ids": list(asset_ids), "w": workspace_id, "p": project_id, **_gate_params(gate)},
            ).fetchall()
        return {row[0] for row in rows}

    @instrument("embedding.vectors.delete", logger)
    def delete_ids(self, ids: Sequence[str], *, workspace_id: str, project_id: str) -> int:
        if not ids:
            return 0
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM embedding_vectors WHERE id = ANY(%s) AND workspace_id = %s "
                "AND project_id = %s",
                (list(ids), workspace_id, project_id),
            ).rowcount

    def delete_ids_in(
        self, generation_id: str, ids: Sequence[str], *, workspace_id: str, project_id: str
    ) -> int:
        if not ids:
            return 0
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM embedding_vectors WHERE generation_id = %s AND id = ANY(%s) "
                "AND workspace_id = %s AND project_id = %s",
                (generation_id, list(ids), workspace_id, project_id),
            ).rowcount

    def delete_kinds_except(
        self,
        generation_id: str,
        kinds: Sequence[str],
        keep_ids: Sequence[str],
        *,
        workspace_id: str,
        project_id: str,
    ) -> int:
        """Remove a project's rows of these kinds that are no longer wanted."""
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM embedding_vectors WHERE generation_id = %s AND workspace_id = %s "
                "AND project_id = %s AND kind = ANY(%s) AND NOT (id = ANY(%s))",
                (generation_id, workspace_id, project_id, list(kinds), list(keep_ids)),
            ).rowcount

    @instrument("embedding.vectors.delete_assets", logger)
    def delete_assets(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_ids: Sequence[str] = (),
        asset_version_ids: Sequence[str] = (),
    ) -> int:
        if not asset_ids and not asset_version_ids:
            return 0
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM embedding_vectors WHERE workspace_id = %s AND project_id = %s "
                "AND (asset_id = ANY(%s) OR asset_version_id = ANY(%s))",
                (workspace_id, project_id, list(asset_ids), list(asset_version_ids)),
            ).rowcount

    def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM embedding_vectors WHERE workspace_id = %s AND project_id = %s",
                (workspace_id, project_id),
            ).rowcount

    def delete_workspace(self, *, workspace_id: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM embedding_vectors WHERE workspace_id = %s", (workspace_id,)
            ).rowcount

    # --- what there is to embed ----------------------------------------------------

    def pictures_ready(self, *, workspace_id: str, project_id: str) -> list[PictureSource]:
        """Current pictures whose web-safe copy exists (media processing made it)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT mv.asset_id, mv.id, s.id, var.object_key, var.content_type, var.checksum "
                "FROM media_variants var "
                "JOIN media_asset_versions mv ON mv.workspace_id = var.workspace_id "
                "AND mv.project_id = var.project_id AND mv.id = var.asset_version_id "
                "JOIN media_assets a ON a.workspace_id = mv.workspace_id "
                "AND a.project_id = mv.project_id AND a.id = mv.asset_id "
                "LEFT JOIN media_segments s ON s.workspace_id = mv.workspace_id "
                "AND s.project_id = mv.project_id AND s.asset_version_id = mv.id "
                "AND s.kind = 'whole' AND s.retired_at IS NULL "
                "WHERE var.workspace_id = %s AND var.project_id = %s AND var.kind = 'web' "
                "AND a.lifecycle = 'active' AND mv.retired_revision IS NULL ORDER BY mv.id",
                (workspace_id, project_id),
            ).fetchall()
        return [
            PictureSource(
                asset_id=row[0],
                asset_version_id=row[1],
                segment_id=row[2],
                object_key=row[3],
                content_type=row[4],
                checksum=row[5],
            )
            for row in rows
        ]

    def accepted_captions(self, *, workspace_id: str, project_id: str) -> list[CaptionSource]:
        """Captions the author accepted or corrected (area C's observations).

        Proposed, rejected and superseded captions are never embedded."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, asset_id, asset_version_id, "
                "COALESCE(NULLIF(corrected_text, ''), text) FROM media_observations "
                "WHERE workspace_id = %s AND project_id = %s AND kind = 'caption' "
                "AND state IN ('accepted', 'corrected') ORDER BY seq",
                (workspace_id, project_id),
            ).fetchall()
        return [
            CaptionSource(
                observation_id=row[0], asset_id=row[1], asset_version_id=row[2], text=row[3]
            )
            for row in rows
            if row[3]
        ]

    def projects(self) -> list[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT workspace_id, project_id FROM embedding_vectors "
                "UNION SELECT workspace_id, project_id FROM text_passages "
                "UNION SELECT workspace_id, project_id FROM media_variants "
                "UNION SELECT workspace_id, project_id FROM media_observations "
                "ORDER BY 1, 2"
            ).fetchall()
        return [(row[0], row[1]) for row in rows if row[0] and row[1]]

    def count(self, generation_id: str, *, workspace_id: str | None = None) -> int:
        with self._connect() as connection:
            if workspace_id is None:
                row = connection.execute(
                    "SELECT count(*) FROM embedding_vectors WHERE generation_id = %s",
                    (generation_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT count(*) FROM embedding_vectors WHERE generation_id = %s "
                    "AND workspace_id = %s",
                    (generation_id, workspace_id),
                ).fetchone()
        return int(row[0]) if row else 0

    # --- a lock across servers -------------------------------------------------------

    def hold_lock(self, name: str) -> Any:
        """Take the named lock without waiting: a handle to give to ``release_lock`` while
        it is held, or None when another server holds it. The lock lives in a session kept
        open for it, so it is let go however that ends, even if the process dies."""
        connection = psycopg.connect(self._dsn, autocommit=True)
        try:
            row = connection.execute(
                "SELECT pg_try_advisory_lock(%s, hashtext(%s))", (_LOCK_SPACE, name)
            ).fetchone()
        except Exception:
            connection.close()
            raise
        if not (row and row[0]):
            connection.close()
            return None
        return connection

    def release_lock(self, held: Any) -> None:
        # Closing the session lets go of the lock it holds.
        held.close()

    def ping(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1 FROM embedding_generations LIMIT 1")
            return True
        except Exception:  # noqa: BLE001 - unreachable is simply down
            return False


__all__ = ["PgEmbeddingIndexStore"]
