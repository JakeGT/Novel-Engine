"""PostgreSQL store for summaries of whole arcs and beats (CTX-006)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.context import PartSummary, SummaryOrigin, SummaryState


def _part(row: Any) -> PartSummary:
    raw = row.basis if isinstance(row.basis, list) else json.loads(row.basis or "[]")
    return PartSummary(
        id=row.id,
        plan_id=row.plan_id,
        body=row.body,
        state=SummaryState(row.state),
        origin=SummaryOrigin(row.origin),
        basis=tuple((str(pair[0]), str(pair[1])) for pair in raw),
        basis_digest=row.basis_digest,
        created_at=row.created_at,
        model=row.model,
        prompt_version=row.prompt_version,
        job_id=row.job_id,
        created_by=row.created_by,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        branch_id=row.branch_id,
    )


class PostgresPartSummaryStore:
    """Summaries of whole arcs and beats, each on the scene summaries it was written from."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def add(self, *, workspace_id: str, project_id: str, summary: PartSummary) -> None:
        async with session_scope(self._session_factory) as db:
            if summary.state is SummaryState.ACCEPTED:
                await self._replace_accepted(db, workspace_id, project_id, summary)
            await db.execute(
                text(
                    "INSERT INTO part_summaries (workspace_id, project_id, id, plan_id, body, "
                    "state, origin, basis, basis_digest, model, prompt_version, job_id, "
                    "created_by, created_at, decided_by, decided_at, branch_id) VALUES (:w, :p, "
                    ":id, :plan, :body, :state, :origin, CAST(:basis AS JSONB), :digest, :model, "
                    ":prompt, :job, :by, :at, :dby, :dat, :b)"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "id": summary.id,
                    "plan": summary.plan_id,
                    "body": summary.body,
                    "state": summary.state.value,
                    "origin": summary.origin.value,
                    "basis": json.dumps([list(pair) for pair in summary.basis]),
                    "digest": summary.basis_digest,
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
        db: Any, workspace_id: str, project_id: str, summary: PartSummary
    ) -> None:
        # One kept summary per part and line: two keeps at once wait for each other here,
        # so the second sees the first and replaces it (the unique index stands behind).
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {
                "key": f"part_summary:{workspace_id}:{project_id}:{summary.plan_id}:"
                f"{summary.branch_id or ''}"
            },
        )
        # Only the one kept for the same part in the same line of the story.
        await db.execute(
            text(
                "UPDATE part_summaries SET state = 'replaced' WHERE workspace_id = :w "
                "AND project_id = :p AND plan_id = :plan AND state = 'accepted' AND id <> :id "
                "AND branch_id IS NOT DISTINCT FROM :b"
            ),
            {
                "w": workspace_id,
                "p": project_id,
                "plan": summary.plan_id,
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
    ) -> PartSummary | None:
        """Accept (perhaps with corrected words) or reject a proposed summary, once."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT * FROM part_summaries WHERE workspace_id = :w AND project_id = :p "
                        "AND id = :id AND state = 'proposed' FOR UPDATE"
                    ),
                    {"w": workspace_id, "p": project_id, "id": summary_id},
                )
            ).first()
            if row is None:
                return None
            current = _part(row)
            if state is SummaryState.ACCEPTED:
                await self._replace_accepted(db, workspace_id, project_id, current)
            updated = (
                await db.execute(
                    text(
                        "UPDATE part_summaries SET state = :state, body = :body, "
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
        return _part(updated) if updated is not None else None

    async def get(
        self, *, workspace_id: str, project_id: str, summary_id: str
    ) -> PartSummary | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT * FROM part_summaries "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": summary_id},
                )
            ).first()
        return _part(row) if row is not None else None

    async def for_plans(
        self, *, workspace_id: str, project_id: str, plan_ids: Sequence[str]
    ) -> list[PartSummary]:
        """Every summary of these parts that is waiting or kept, in every line of the
        story, oldest first."""
        if not plan_ids:
            return []
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT * FROM part_summaries WHERE workspace_id = :w "
                        "AND project_id = :p AND plan_id = ANY(:ids) "
                        "AND state IN ('proposed', 'accepted') ORDER BY created_at, id"
                    ),
                    {"w": workspace_id, "p": project_id, "ids": list(plan_ids)},
                )
            ).all()
        return [_part(row) for row in rows]


__all__ = ["PostgresPartSummaryStore"]
