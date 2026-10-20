"""PostgreSQL job store: claimed under a lease with FOR UPDATE SKIP LOCKED (ADR-022).

Several workers can poll safely: each claim locks one row and skips rows another
worker holds. A job whose lease runs out (its worker died) is claimable again, up to
``MAX_ATTEMPTS``; after that it is marked failed rather than retried for ever.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.identity import AuthMode
from backend.domain.jobs import (
    ANY_BRANCH,
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    UNCOUNTED_KINDS,
    Job,
    JobStatus,
    LeaseLost,
)

_COLUMNS = (
    "workspace_id, project_id, id, kind, status, stage, progress, payload, result, "
    "actor_user_id, auth_mode, policy_version, role, correlation_id, idempotency_key, "
    "cancel_requested, attempts, error_code, error_message, prompt_tokens, completion_tokens, "
    "cached_prompt_tokens, provider_cost_usd, created_at, updated_at, finished_at"
)


def _load(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return json.loads(value) if value else {}


def _job(row: Any) -> Job:
    return Job(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        id=row.id,
        kind=row.kind,
        status=JobStatus(row.status),
        stage=row.stage,
        progress=int(row.progress),
        payload=_load(row.payload),
        result=_load(row.result),
        actor_user_id=row.actor_user_id,
        auth_mode=AuthMode(row.auth_mode),
        policy_version=row.policy_version,
        role=row.role,
        correlation_id=row.correlation_id,
        idempotency_key=row.idempotency_key,
        cancel_requested=bool(row.cancel_requested),
        attempts=int(row.attempts),
        error_code=row.error_code,
        error_message=row.error_message,
        prompt_tokens=int(row.prompt_tokens),
        completion_tokens=int(row.completion_tokens),
        cached_prompt_tokens=int(row.cached_prompt_tokens),
        provider_cost_usd=(
            float(row.provider_cost_usd) if row.provider_cost_usd is not None else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


class PostgresJobStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def create(self, job: Job) -> Job:
        """Store a new job; the same idempotency key returns the job already stored.

        Two requests with the same key at the same moment (a double click) both get the
        one job: the second insert yields to the first rather than failing.
        """
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "INSERT INTO jobs (workspace_id, project_id, id, kind, payload, "
                        "actor_user_id, auth_mode, policy_version, role, correlation_id, "
                        "idempotency_key) VALUES (:w, :p, :id, :kind, CAST(:payload AS JSONB), "
                        ":a, :mode, :policy, :role, :corr, :k) "
                        "ON CONFLICT (workspace_id, project_id, actor_user_id, idempotency_key) "
                        "WHERE idempotency_key <> '' DO NOTHING "
                        f"RETURNING {_COLUMNS}"
                    ),
                    {
                        "w": job.workspace_id,
                        "p": job.project_id,
                        "id": job.id,
                        "kind": job.kind,
                        "payload": json.dumps(job.payload),
                        "a": job.actor_user_id,
                        "mode": job.auth_mode.value,
                        "policy": job.policy_version,
                        "role": job.role,
                        "corr": job.correlation_id,
                        "k": job.idempotency_key,
                    },
                )
            ).first()
            if row is None:
                row = (
                    await db.execute(
                        text(
                            f"SELECT {_COLUMNS} FROM jobs WHERE workspace_id = :w "
                            "AND project_id = :p AND actor_user_id = :a AND idempotency_key = :k"
                        ),
                        {
                            "w": job.workspace_id,
                            "p": job.project_id,
                            "a": job.actor_user_id,
                            "k": job.idempotency_key,
                        },
                    )
                ).one()
        return _job(row)

    async def get(self, *, workspace_id: str, project_id: str, job_id: str) -> Job | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM jobs "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": job_id},
                )
            ).first()
        return _job(row) if row is not None else None

    async def find_by_key(
        self, *, workspace_id: str, project_id: str, actor_user_id: str, idempotency_key: str
    ) -> Job | None:
        """The job this person already asked for with this key, if any."""
        if not idempotency_key:
            return None
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM jobs WHERE workspace_id = :w "
                        "AND project_id = :p AND actor_user_id = :a AND idempotency_key = :k"
                    ),
                    {"w": workspace_id, "p": project_id, "a": actor_user_id, "k": idempotency_key},
                )
            ).first()
        return _job(row) if row is not None else None

    async def waiting(self) -> tuple[int, float | None]:
        """(how many jobs wait to start, seconds the oldest has waited), across every story.
        For the readiness check: counts only, never what a job is about."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT count(*), EXTRACT(EPOCH FROM now() - min(created_at)) "
                        "FROM jobs WHERE status = 'queued'"
                    )
                )
            ).one()
        count = int(row[0])
        return count, (max(0.0, float(row[1])) if count and row[1] is not None else None)

    async def count_active(self, *, workspace_id: str, project_id: str, actor_user_id: str) -> int:
        """How many of this person's jobs in the project are waiting or under way."""
        async with session_scope(self._session_factory) as db:
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM jobs WHERE workspace_id = :w AND project_id = :p "
                        "AND actor_user_id = :a AND status IN ('queued', 'running') "
                        "AND NOT (kind = ANY(:uncounted))"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "a": actor_user_id,
                        "uncounted": sorted(UNCOUNTED_KINDS),
                    },
                )
            ).scalar_one()
        return int(count)

    async def recent(
        self,
        *,
        workspace_id: str,
        project_id: str,
        kind: str,
        plan_id: str,
        limit: int = 10,
        branch_id: str | None = ANY_BRANCH,
    ) -> list[Job]:
        """A scene's jobs, newest first. ``branch_id`` keeps only those asked for in that
        line of the story (None is the main story); ``ANY_BRANCH`` keeps them all."""
        where = ""
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "kind": kind,
            "plan": plan_id,
            "limit": limit,
        }
        if branch_id != ANY_BRANCH:
            where = "AND payload->>'branch_id' IS NOT DISTINCT FROM :b "
            params["b"] = branch_id
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM jobs WHERE workspace_id = :w AND project_id = :p "
                        f"AND kind = :kind AND payload->>'plan_id' = :plan {where}"
                        "ORDER BY created_at DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return [_job(row) for row in rows]

    async def claim(self, *, owner: str) -> Job | None:
        """Take the oldest job that is waiting, or whose worker stopped renewing its lease."""
        async with session_scope(self._session_factory) as db:
            # Jobs interrupted too often are given up, not tried again.
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'failed', error_code = 'interrupted', "
                    "error_message = 'The work was interrupted and could not be finished.', "
                    "finished_at = now(), updated_at = now(), lease_owner = NULL "
                    "WHERE status = 'running' AND lease_expires_at < now() AND attempts >= :max"
                ),
                {"max": MAX_ATTEMPTS},
            )
            row = (
                await db.execute(
                    text(
                        f"""
                        UPDATE jobs SET status = 'running', lease_owner = :owner,
                            lease_expires_at = now() + make_interval(secs => :lease),
                            attempts = attempts + 1, updated_at = now()
                        WHERE (workspace_id, project_id, id) = (
                            SELECT workspace_id, project_id, id FROM jobs
                            WHERE status = 'queued'
                               OR (status = 'running' AND lease_expires_at < now())
                            ORDER BY created_at
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        RETURNING {_COLUMNS}
                        """
                    ),
                    {"owner": owner, "lease": LEASE_SECONDS},
                )
            ).first()
        return _job(row) if row is not None else None

    async def heartbeat(self, job: Job, *, owner: str, stage: str, progress: int) -> bool:
        """Record progress and renew the lease. True when the author asked it to stop.

        Raises ``LeaseLost`` when this worker no longer holds the job: another worker
        took it over, or it was given up."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "UPDATE jobs SET stage = :stage, progress = :progress, updated_at = now(), "
                        "lease_expires_at = now() + make_interval(secs => :lease) "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id "
                        "AND lease_owner = :owner AND status = 'running' "
                        "RETURNING cancel_requested"
                    ),
                    {
                        "stage": stage[:120],
                        "progress": max(0, min(100, progress)),
                        "lease": LEASE_SECONDS,
                        "w": job.workspace_id,
                        "p": job.project_id,
                        "id": job.id,
                        "owner": owner,
                    },
                )
            ).first()
        if row is None:
            raise LeaseLost(job.id)
        return bool(row.cancel_requested)

    async def add_usage(
        self,
        job: Job,
        *,
        owner: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int = 0,
        cost_usd: float | None = None,
    ) -> None:
        """Add to the job's token counts (and the cost its provider reported, if any),
        only while this worker still holds it."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE jobs SET prompt_tokens = prompt_tokens + :pt, "
                    "completion_tokens = completion_tokens + :ct, "
                    "cached_prompt_tokens = cached_prompt_tokens + :cached, "
                    "provider_cost_usd = CASE WHEN CAST(:cost AS DOUBLE PRECISION) IS NULL "
                    "THEN provider_cost_usd ELSE coalesce(provider_cost_usd, 0) + "
                    "CAST(:cost AS DOUBLE PRECISION) END, updated_at = now() "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id "
                    "AND lease_owner = :owner"
                ),
                {
                    "pt": prompt_tokens,
                    "ct": completion_tokens,
                    "cached": max(0, cached_prompt_tokens),
                    "cost": cost_usd if cost_usd is not None and cost_usd >= 0 else None,
                    "w": job.workspace_id,
                    "p": job.project_id,
                    "id": job.id,
                    "owner": owner,
                },
            )

    async def requeue(self, job: Job, *, owner: str) -> None:
        """Put a job this worker holds back in the queue (the app is stopping), without
        counting the interrupted run as an attempt."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'queued', lease_owner = NULL, "
                    "lease_expires_at = NULL, attempts = GREATEST(attempts - 1, 0), "
                    "updated_at = now() WHERE workspace_id = :w AND project_id = :p "
                    "AND id = :id AND lease_owner = :owner AND status = 'running'"
                ),
                {"w": job.workspace_id, "p": job.project_id, "id": job.id, "owner": owner},
            )

    async def finish(
        self,
        job: Job,
        *,
        owner: str,
        status: JobStatus,
        result: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Job:
        """Record how the job ended, only while this worker holds it. A job that has
        already ended keeps its ending, and a worker that lost its lease never ends the
        run of the worker that took over (nor a job that was given up)."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "UPDATE jobs SET status = :status, result = CAST(:result AS JSONB), "
                        "error_code = :code, error_message = :message, progress = "
                        "CASE WHEN :status = 'succeeded' THEN 100 ELSE progress END, "
                        "lease_owner = NULL, lease_expires_at = NULL, finished_at = now(), "
                        "updated_at = now() WHERE workspace_id = :w AND project_id = :p "
                        "AND id = :id AND status = 'running' AND lease_owner = :owner "
                        f"RETURNING {_COLUMNS}"
                    ),
                    {
                        "status": status.value,
                        "result": json.dumps(result or {}),
                        "code": error_code,
                        "message": error_message,
                        "owner": owner,
                        "w": job.workspace_id,
                        "p": job.project_id,
                        "id": job.id,
                    },
                )
            ).first()
            if row is None:
                row = (
                    await db.execute(
                        text(
                            f"SELECT {_COLUMNS} FROM jobs "
                            "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                        ),
                        {"w": job.workspace_id, "p": job.project_id, "id": job.id},
                    )
                ).one()
        return _job(row)

    async def request_cancel(
        self, *, workspace_id: str, project_id: str, job_id: str
    ) -> Job | None:
        """A waiting job is cancelled at once; a running one stops at its next step."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        # A job that has already ended is left as it ended.
                        "UPDATE jobs SET updated_at = now(), cancel_requested = "
                        "CASE WHEN status IN ('queued', 'running') THEN true "
                        "ELSE cancel_requested END, "
                        "status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END, "
                        "finished_at = CASE WHEN status = 'queued' THEN now() ELSE finished_at END "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id "
                        f"RETURNING {_COLUMNS}"
                    ),
                    {"w": workspace_id, "p": project_id, "id": job_id},
                )
            ).first()
        return _job(row) if row is not None else None


__all__ = ["PostgresJobStore"]
