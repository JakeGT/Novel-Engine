"""PostgreSQL store for checks kept as runs (CON-001, migration 0044)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.consistency_runs import ConsistencyRun, RecordedCheck, RunInput

_RUN = (
    "SELECT workspace_id, project_id, id, branch_id, kind, scope, plan_id, story_revision, "
    "inputs, passes, findings, job_id, created_by, created_at FROM consistency_runs"
)


def _load(value: Any) -> Any:
    if value is None:
        return []
    return json.loads(value) if isinstance(value, str) else value


def _run(row: Any) -> ConsistencyRun:
    return ConsistencyRun(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        branch_id=row[3],
        kind=row[4],
        scope=row[5],
        plan_id=row[6],
        story_revision=int(row[7]),
        inputs=tuple(RunInput.from_json(item) for item in _load(row[8])),
        passes=tuple(str(item) for item in _load(row[9])),
        findings=tuple(RecordedCheck.from_json(item) for item in _load(row[10])),
        job_id=row[11],
        created_by=row[12],
        created_at=row[13],
    )


class PostgresConsistencyRunStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def add(self, run: ConsistencyRun) -> bool:
        """Keep a run. A second run for the same job is not kept (False)."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "INSERT INTO consistency_runs (workspace_id, project_id, id, branch_id, kind, "
                    "scope, plan_id, story_revision, inputs, passes, findings, job_id, "
                    "created_by) VALUES (:w, :p, :id, :b, :kind, :scope, :plan, :rev, "
                    "CAST(:inputs AS JSONB), CAST(:passes AS JSONB), CAST(:findings AS JSONB), "
                    ":job, :by) ON CONFLICT DO NOTHING"
                ),
                {
                    "w": run.workspace_id,
                    "p": run.project_id,
                    "id": run.id,
                    "b": run.branch_id,
                    "kind": run.kind,
                    "scope": run.scope,
                    "plan": run.plan_id,
                    "rev": run.story_revision,
                    "inputs": json.dumps([item.as_json() for item in run.inputs]),
                    "passes": json.dumps(list(run.passes)),
                    "findings": json.dumps([item.as_json() for item in run.findings]),
                    "job": run.job_id,
                    "by": run.created_by,
                },
            )
        return bool(getattr(result, "rowcount", 1))

    async def get(
        self, *, workspace_id: str, project_id: str, run_id: str
    ) -> ConsistencyRun | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(_RUN + " WHERE workspace_id = :w AND project_id = :p AND id = :id"),
                    {"w": workspace_id, "p": project_id, "id": run_id},
                )
            ).first()
        return _run(row) if row is not None else None

    async def for_job(
        self, *, workspace_id: str, project_id: str, job_id: str
    ) -> ConsistencyRun | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(_RUN + " WHERE workspace_id = :w AND project_id = :p AND job_id = :job"),
                    {"w": workspace_id, "p": project_id, "job": job_id},
                )
            ).first()
        return _run(row) if row is not None else None

    async def recent(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        plan_id: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[ConsistencyRun]:
        """The newest runs in one line of the story; with ``plan_id``, those that read
        that scene (an audit of a part of the story included)."""
        clauses = ["workspace_id = :w", "project_id = :p", "branch_id = :b"]
        params: dict[str, Any] = {"w": workspace_id, "p": project_id, "b": branch_id, "n": limit}
        if plan_id is not None:
            clauses.append("inputs @> CAST(:touch AS JSONB)")
            params["touch"] = json.dumps([{"plan_id": plan_id}])
        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = kind
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _RUN + f" WHERE {' AND '.join(clauses)} "  # noqa: S608 - fixed clauses
                        "ORDER BY created_at DESC, id DESC LIMIT :n"
                    ),
                    params,
                )
            ).all()
        return [_run(row) for row in rows]


__all__ = ["PostgresConsistencyRunStore"]
