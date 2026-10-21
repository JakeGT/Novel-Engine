"""PostgreSQL stores for the passage index and context manifests (ADR-021)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.context import (
    IndexWaiting,
    SavedManifest,
    SceneSummary,
    SummaryOrigin,
    SummaryState,
    WaitingText,
)
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
            branch_id=row.branch_id,
        )


class PostgresPassageQueue:
    """The passage index's waiting list (CTX-005): one row per text, coalescing.

    Asking again for a text already waiting raises its generation and keeps when it was
    first asked for, so the lag is measured from the oldest change not yet read. A
    delivery removes the row only when the generation it read is still the latest.
    Claims use ``FOR UPDATE SKIP LOCKED`` and a lease, so two servers never read the
    same text at once and a server that dies mid-read lets go when its lease ends.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def request(
        self, *, workspace_id: str, project_id: str, source_id: str, gone: bool = False
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO passage_index_waiting (workspace_id, project_id, source_id, gone) "
                    "VALUES (:w, :p, :s, :gone) ON CONFLICT (workspace_id, project_id, source_id) "
                    "DO UPDATE SET gone = EXCLUDED.gone, "
                    "generation = passage_index_waiting.generation + 1, requested_at = now(), "
                    # A fresh change is read soon, never after an old failure's wait; a
                    # text being read keeps its claim (no failure: attempts is 0).
                    "claimed_until = CASE WHEN passage_index_waiting.attempts > 0 THEN NULL "
                    "ELSE passage_index_waiting.claimed_until END, "
                    "attempts = 0, last_error = NULL"
                ),
                {"w": workspace_id, "p": project_id, "s": source_id, "gone": gone},
            )

    async def claim(
        self, *, workspace_id: str, project_id: str, limit: int, lease_seconds: float
    ) -> list[WaitingText]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        # MATERIALIZED: the pick runs once. As a plain subquery the
                        # planner may run it again inside the update, which then claims
                        # more rows than ``limit`` (a known SKIP LOCKED pitfall).
                        "WITH pick AS MATERIALIZED (SELECT source_id FROM passage_index_waiting "
                        "WHERE workspace_id = :w AND project_id = :p "
                        "AND (claimed_until IS NULL OR claimed_until < now()) "
                        "ORDER BY first_requested_at, source_id LIMIT :limit "
                        "FOR UPDATE SKIP LOCKED) "
                        "UPDATE passage_index_waiting q "
                        "SET claimed_until = now() + make_interval(secs => :lease) "
                        "FROM pick "
                        "WHERE q.workspace_id = :w AND q.project_id = :p "
                        "AND q.source_id = pick.source_id "
                        "RETURNING q.source_id, q.gone, q.generation, q.attempts"
                    ),
                    {"w": workspace_id, "p": project_id, "limit": limit, "lease": lease_seconds},
                )
            ).all()
        return [
            WaitingText(
                source_id=row.source_id,
                gone=bool(row.gone),
                generation=int(row.generation),
                attempts=int(row.attempts),
            )
            for row in sorted(rows, key=lambda row: row.source_id)
        ]

    async def done(
        self, *, workspace_id: str, project_id: str, source_id: str, generation: int
    ) -> bool:
        """Remove the row if nothing changed since it was read; else let it go again."""
        scope = {"w": workspace_id, "p": project_id, "s": source_id, "g": generation}
        async with session_scope(self._session_factory) as db:
            removed = await db.execute(
                text(
                    "DELETE FROM passage_index_waiting WHERE workspace_id = :w "
                    "AND project_id = :p AND source_id = :s AND generation = :g"
                ),
                scope,
            )
            if removed.rowcount:  # type: ignore[attr-defined]
                return True
            await db.execute(
                text(
                    "UPDATE passage_index_waiting SET claimed_until = NULL WHERE workspace_id = :w "
                    "AND project_id = :p AND source_id = :s"
                ),
                scope,
            )
        return False

    async def failed(
        self,
        *,
        workspace_id: str,
        project_id: str,
        source_id: str,
        generation: int,
        error: str,
        retry_after_seconds: float,
    ) -> bool:
        """Count a failed read and wait before the next, if nothing changed since it was
        read; a newer change is let go at once instead, to be read soon. True if counted."""
        scope = {"w": workspace_id, "p": project_id, "s": source_id, "g": generation}
        async with session_scope(self._session_factory) as db:
            counted = await db.execute(
                text(
                    "UPDATE passage_index_waiting SET attempts = attempts + 1, last_error = :e, "
                    "claimed_until = now() + make_interval(secs => :after) "
                    "WHERE workspace_id = :w AND project_id = :p AND source_id = :s "
                    "AND generation = :g"
                ),
                {**scope, "e": error[:200], "after": retry_after_seconds},
            )
            if counted.rowcount:  # type: ignore[attr-defined]
                return True
            await db.execute(
                text(
                    "UPDATE passage_index_waiting SET claimed_until = NULL WHERE workspace_id = :w "
                    "AND project_id = :p AND source_id = :s"
                ),
                scope,
            )
        return False

    async def waiting(self, *, workspace_id: str, project_id: str) -> IndexWaiting:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT count(*) AS n, "
                        "EXTRACT(EPOCH FROM now() - min(first_requested_at)) AS oldest, "
                        "count(*) FILTER (WHERE attempts > 0) AS failing "
                        "FROM passage_index_waiting WHERE workspace_id = :w AND project_id = :p"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).first()
        if row is None or not row.n:
            return IndexWaiting()
        return IndexWaiting(
            count=int(row.n),
            oldest_seconds=max(0.0, float(row.oldest or 0.0)),
            failing=int(row.failing or 0),
        )

    async def lag(self) -> IndexWaiting:
        """How far behind the passage index is across every story on this server
        (``/health/ready``): counts and the oldest wait only, never which stories."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT count(*) AS n, "
                        "EXTRACT(EPOCH FROM now() - min(first_requested_at)) AS oldest, "
                        "count(*) FILTER (WHERE attempts > 0) AS failing "
                        "FROM passage_index_waiting"
                    )
                )
            ).first()
        if row is None or not row.n:
            return IndexWaiting()
        return IndexWaiting(
            count=int(row.n),
            oldest_seconds=max(0.0, float(row.oldest or 0.0)),
            failing=int(row.failing or 0),
        )

    async def stories_waiting(self, *, limit: int) -> list[tuple[str, str]]:
        """Stories with a text ready to read now, oldest change first."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT workspace_id, project_id, min(first_requested_at) AS oldest "
                        "FROM passage_index_waiting "
                        "WHERE claimed_until IS NULL OR claimed_until < now() "
                        "GROUP BY workspace_id, project_id ORDER BY oldest LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ).all()
        return [(str(row.workspace_id), str(row.project_id)) for row in rows]


def _summary(row: Any) -> SceneSummary:
    return SceneSummary(
        id=row.id,
        plan_id=row.plan_id,
        source_id=row.source_id,
        version_id=row.version_id,
        body=row.body,
        state=SummaryState(row.state),
        origin=SummaryOrigin(row.origin),
        created_at=row.created_at,
        model=row.model,
        prompt_version=row.prompt_version,
        job_id=row.job_id,
        created_by=row.created_by,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        branch_id=row.branch_id,
    )


class PostgresSceneSummaryStore:
    """Summaries of scenes' prose (CTX-006), each of one exact version."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def add(self, *, workspace_id: str, project_id: str, summary: SceneSummary) -> None:
        async with session_scope(self._session_factory) as db:
            if summary.state is SummaryState.ACCEPTED:
                await self._replace_accepted(db, workspace_id, project_id, summary)
            await db.execute(
                text(
                    "INSERT INTO scene_summaries (workspace_id, project_id, id, plan_id, "
                    "source_id, version_id, body, state, origin, model, prompt_version, job_id, "
                    "created_by, created_at, decided_by, decided_at, branch_id) VALUES (:w, :p, "
                    ":id, :plan, :s, :v, :body, :state, :origin, :model, :prompt, :job, :by, :at, "
                    ":dby, :dat, :b)"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "id": summary.id,
                    "plan": summary.plan_id,
                    "s": summary.source_id,
                    "v": summary.version_id,
                    "body": summary.body,
                    "state": summary.state.value,
                    "origin": summary.origin.value,
                    "model": summary.model,
                    "prompt": summary.prompt_version,
                    "job": summary.job_id,
                    "by": summary.created_by,
                    "at": summary.created_at,
                    "dby": summary.decided_by,
                    "dat": summary.decided_at,
                    "b": summary.branch_id,
                },
            )

    @staticmethod
    async def _replace_accepted(
        db: Any, workspace_id: str, project_id: str, summary: SceneSummary
    ) -> None:
        # Only the one kept in the same line of the story: a what-if's never replaces
        # the main story's, nor the other way round.
        await db.execute(
            text(
                "UPDATE scene_summaries SET state = 'replaced' WHERE workspace_id = :w "
                "AND project_id = :p AND source_id = :s AND state = 'accepted' AND id <> :id "
                "AND branch_id IS NOT DISTINCT FROM :b"
            ),
            {
                "w": workspace_id,
                "p": project_id,
                "s": summary.source_id,
                "id": summary.id,
                "b": summary.branch_id,
            },
        )

    async def decide(
        self,
        *,
        workspace_id: str,
        project_id: str,
        summary_id: str,
        state: SummaryState,
        body: str | None,
        origin: SummaryOrigin | None,
        decided_by: str | None,
        decided_at: Any,
    ) -> SceneSummary | None:
        """Accept (perhaps with corrected words) or reject a proposed summary, once."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT * FROM scene_summaries WHERE workspace_id = :w AND project_id = :p "
                        "AND id = :id AND state = 'proposed' FOR UPDATE"
                    ),
                    {"w": workspace_id, "p": project_id, "id": summary_id},
                )
            ).first()
            if row is None:
                return None
            current = _summary(row)
            if state is SummaryState.ACCEPTED:
                await self._replace_accepted(db, workspace_id, project_id, current)
            updated = (
                await db.execute(
                    text(
                        "UPDATE scene_summaries SET state = :state, body = :body, "
                        "origin = :origin, decided_by = :by, decided_at = :at "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id RETURNING *"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "id": summary_id,
                        "state": state.value,
                        "body": body if body is not None else current.body,
                        "origin": (origin or current.origin).value,
                        "by": decided_by,
                        "at": decided_at,
                    },
                )
            ).first()
        return _summary(updated) if updated is not None else None

    async def get(
        self, *, workspace_id: str, project_id: str, summary_id: str
    ) -> SceneSummary | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT * FROM scene_summaries "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": summary_id},
                )
            ).first()
        return _summary(row) if row is not None else None

    async def for_sources(
        self, *, workspace_id: str, project_id: str, source_ids: Sequence[str]
    ) -> list[SceneSummary]:
        """Every summary of these texts that is waiting or kept, in every line of the
        story, oldest first; each says which line it belongs to."""
        if not source_ids:
            return []
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT * FROM scene_summaries WHERE workspace_id = :w "
                        "AND project_id = :p AND source_id = ANY(:ids) "
                        "AND state IN ('proposed', 'accepted') ORDER BY created_at, id"
                    ),
                    {"w": workspace_id, "p": project_id, "ids": list(source_ids)},
                )
            ).all()
        return [_summary(row) for row in rows]


__all__ = [
    "PostgresManifestStore",
    "PostgresPassageQueue",
    "PostgresPassageStore",
    "PostgresSceneSummaryStore",
]
