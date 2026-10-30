"""PostgreSQL stores for the author's usual voice, audits on a schedule and correction
plans (CON-018, CON-011; migration 0055)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.audit_plans import AuditSchedule, CorrectionItem, CorrectionPlan


def _load(value: Any) -> Any:
    if value is None:
        return []
    return json.loads(value) if isinstance(value, str) else value


class PostgresVoiceBaselineStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def left_out(self, *, workspace_id: str, project_id: str) -> list[str]:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT left_out FROM voice_baselines "
                        "WHERE workspace_id = :w AND project_id = :p"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).first()
        return [str(item) for item in _load(row[0])] if row is not None else []

    async def set_left_out(
        self, *, workspace_id: str, project_id: str, plan_ids: list[str], actor_user_id: str | None
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO voice_baselines (workspace_id, project_id, left_out, updated_by, "
                    "updated_at) VALUES (:w, :p, CAST(:ids AS JSONB), :by, now()) "
                    "ON CONFLICT (workspace_id, project_id) DO UPDATE SET "
                    "left_out = EXCLUDED.left_out, updated_by = EXCLUDED.updated_by, "
                    "updated_at = now()"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "ids": json.dumps(plan_ids),
                    "by": actor_user_id,
                },
            )


_SCHEDULE = (
    "SELECT workspace_id, project_id, id, branch_id, plan_id, passes, every_days, max_tokens, "
    "actor_user_id, auth_mode, next_run_at, enabled, last_run_at, last_job_id, last_outcome, "
    "last_inputs, created_at, updated_at FROM audit_schedules"
)
_RETURNING = (
    "workspace_id, project_id, id, branch_id, plan_id, passes, every_days, max_tokens, "
    "actor_user_id, auth_mode, next_run_at, enabled, last_run_at, last_job_id, last_outcome, "
    "last_inputs, created_at, updated_at"
)


def _schedule(row: Any) -> AuditSchedule:
    return AuditSchedule(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        branch_id=row[3],
        plan_id=row[4] or "",
        passes=tuple(str(item) for item in _load(row[5])),
        every_days=int(row[6]),
        max_tokens=int(row[7]),
        actor_user_id=row[8],
        auth_mode=row[9],
        next_run_at=row[10],
        enabled=bool(row[11]),
        last_run_at=row[12],
        last_job_id=row[13],
        last_outcome=row[14],
        last_inputs=row[15],
        created_at=row[16],
        updated_at=row[17],
    )


class PostgresAuditScheduleStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def all_for(self, *, workspace_id: str, project_id: str) -> list[AuditSchedule]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _SCHEDULE + " WHERE workspace_id = :w AND project_id = :p "
                        "ORDER BY created_at, id"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return [_schedule(row) for row in rows]

    async def get(
        self, *, workspace_id: str, project_id: str, schedule_id: str
    ) -> AuditSchedule | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(_SCHEDULE + " WHERE workspace_id = :w AND project_id = :p AND id = :id"),
                    {"w": workspace_id, "p": project_id, "id": schedule_id},
                )
            ).first()
        return _schedule(row) if row is not None else None

    async def save(self, schedule: AuditSchedule) -> AuditSchedule:
        """Add a schedule, or replace the one for the same part of the same line."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "INSERT INTO audit_schedules (workspace_id, project_id, id, branch_id, "
                        "plan_id, passes, every_days, max_tokens, actor_user_id, auth_mode, "
                        "enabled, next_run_at) VALUES (:w, :p, :id, :b, :plan, "
                        "CAST(:passes AS JSONB), :every, :most, :actor, :mode, :on, :next) "
                        "ON CONFLICT (workspace_id, project_id, (COALESCE(branch_id, '')), plan_id) "
                        "DO UPDATE SET passes = EXCLUDED.passes, every_days = EXCLUDED.every_days, "
                        "max_tokens = EXCLUDED.max_tokens, actor_user_id = "
                        "EXCLUDED.actor_user_id, auth_mode = EXCLUDED.auth_mode, enabled = "
                        "EXCLUDED.enabled, next_run_at = EXCLUDED.next_run_at, updated_at = now() "
                        f"RETURNING {_RETURNING}"
                    ),
                    {
                        "w": schedule.workspace_id,
                        "p": schedule.project_id,
                        "id": schedule.id,
                        "b": schedule.branch_id,
                        "plan": schedule.plan_id,
                        "passes": json.dumps(list(schedule.passes)),
                        "every": schedule.every_days,
                        "most": schedule.max_tokens,
                        "actor": schedule.actor_user_id,
                        "mode": schedule.auth_mode,
                        "on": schedule.enabled,
                        "next": schedule.next_run_at,
                    },
                )
            ).first()
        return _schedule(row)

    async def delete(self, *, workspace_id: str, project_id: str, schedule_id: str) -> bool:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM audit_schedules WHERE workspace_id = :w AND project_id = :p "
                    "AND id = :id"
                ),
                {"w": workspace_id, "p": project_id, "id": schedule_id},
            )
        return bool(getattr(result, "rowcount", 0))

    async def delete_for_branch(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        """Remove every schedule of one what-if (when it is discarded). Returns how many."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM audit_schedules WHERE workspace_id = :w AND project_id = :p "
                    "AND branch_id = :b"
                ),
                {"w": workspace_id, "p": project_id, "b": branch_id},
            )
        return int(getattr(result, "rowcount", 0) or 0)

    async def claim_due(
        self, *, now: datetime, hold_until: datetime, limit: int = 20
    ) -> list[AuditSchedule]:
        """Schedules due now, each moved to ``hold_until`` in the same statement, so two
        servers never take the same run; a run that is never recorded comes due again."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "UPDATE audit_schedules AS s SET next_run_at = :hold FROM ("
                        "SELECT workspace_id, project_id, id FROM audit_schedules "
                        "WHERE enabled AND next_run_at <= :now ORDER BY next_run_at "
                        "LIMIT :n FOR UPDATE SKIP LOCKED) AS due "
                        "WHERE s.workspace_id = due.workspace_id AND s.project_id = "
                        "due.project_id AND s.id = due.id "
                        "RETURNING "
                        + ", ".join(f"s.{column.strip()}" for column in _RETURNING.split(","))
                    ),
                    {"now": now, "hold": hold_until, "n": limit},
                )
            ).all()
        return [_schedule(row) for row in rows]

    async def record_run(
        self,
        *,
        workspace_id: str,
        project_id: str,
        schedule_id: str,
        ran_at: datetime,
        outcome: str,
        next_run_at: datetime,
        job_id: str | None = None,
        inputs: str | None = None,
        enabled: bool = True,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE audit_schedules SET last_run_at = :at, last_outcome = :outcome, "
                    "next_run_at = :next, enabled = :on, "
                    "last_job_id = COALESCE(:job, last_job_id), "
                    "last_inputs = COALESCE(:inputs, last_inputs), updated_at = now() "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "id": schedule_id,
                    "at": ran_at,
                    "outcome": outcome,
                    "next": next_run_at,
                    "on": enabled,
                    "job": job_id,
                    "inputs": inputs,
                },
            )


_PLAN_COLUMNS = (
    "workspace_id, project_id, id, branch_id, audit_job_id, job_id, summary, items, "
    "model, prompt_version, dropped, created_by, created_at, updated_at"
)
_PLAN = f"SELECT {_PLAN_COLUMNS} FROM correction_plans"


def _plan(row: Any) -> CorrectionPlan:
    return CorrectionPlan(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        branch_id=row[3],
        audit_job_id=row[4],
        job_id=row[5],
        summary=row[6] or "",
        items=tuple(CorrectionItem.from_json(item) for item in _load(row[7])),
        model=row[8],
        prompt_version=row[9],
        dropped=int(row[10] or 0),
        created_by=row[11],
        created_at=row[12],
        updated_at=row[13],
    )


class PostgresCorrectionPlanStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def add(self, plan: CorrectionPlan) -> CorrectionPlan:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO correction_plans (workspace_id, project_id, id, branch_id, "
                    "audit_job_id, job_id, summary, items, model, prompt_version, dropped, "
                    "created_by) VALUES (:w, :p, :id, :b, :audit, :job, :summary, "
                    "CAST(:items AS JSONB), :model, :prompt, :dropped, :by) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "w": plan.workspace_id,
                    "p": plan.project_id,
                    "id": plan.id,
                    "b": plan.branch_id,
                    "audit": plan.audit_job_id,
                    "job": plan.job_id,
                    "summary": plan.summary,
                    "items": json.dumps([item.as_json() for item in plan.items]),
                    "model": plan.model,
                    "prompt": plan.prompt_version,
                    "dropped": plan.dropped,
                    "by": plan.created_by,
                },
            )
        found = await self.get(
            workspace_id=plan.workspace_id, project_id=plan.project_id, plan_id=plan.id
        )
        assert found is not None
        return found

    async def get(
        self, *, workspace_id: str, project_id: str, plan_id: str
    ) -> CorrectionPlan | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(_PLAN + " WHERE workspace_id = :w AND project_id = :p AND id = :id"),
                    {"w": workspace_id, "p": project_id, "id": plan_id},
                )
            ).first()
        return _plan(row) if row is not None else None

    async def for_job(
        self, *, workspace_id: str, project_id: str, job_id: str
    ) -> CorrectionPlan | None:
        """The plan a planning job wrote, if it wrote one (a job taken over writes it once)."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(_PLAN + " WHERE workspace_id = :w AND project_id = :p AND job_id = :job"),
                    {"w": workspace_id, "p": project_id, "job": job_id},
                )
            ).first()
        return _plan(row) if row is not None else None

    async def for_audit(
        self, *, workspace_id: str, project_id: str, audit_job_id: str
    ) -> list[CorrectionPlan]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _PLAN + " WHERE workspace_id = :w AND project_id = :p "
                        "AND audit_job_id = :audit ORDER BY created_at DESC, id DESC LIMIT 10"
                    ),
                    {"w": workspace_id, "p": project_id, "audit": audit_job_id},
                )
            ).all()
        return [_plan(row) for row in rows]

    async def set_decision(
        self, *, workspace_id: str, project_id: str, plan_id: str, index: int, decision: str
    ) -> CorrectionPlan | None:
        """Set one step's decision in place, in one statement, so two decisions made at
        once on different steps both stand. None when the plan or the step is not there."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "UPDATE correction_plans SET items = jsonb_set(items, "
                        "ARRAY[CAST(:i AS TEXT), 'decision'], to_jsonb(CAST(:d AS TEXT))), "
                        "updated_at = now() WHERE workspace_id = :w AND project_id = :p "
                        "AND id = :id AND :i >= 0 AND jsonb_array_length(items) > :i "
                        f"RETURNING {_PLAN_COLUMNS}"
                    ),
                    {"w": workspace_id, "p": project_id, "id": plan_id, "i": index, "d": decision},
                )
            ).first()
        return _plan(row) if row is not None else None

    async def set_items(
        self, *, workspace_id: str, project_id: str, plan_id: str, items: list[CorrectionItem]
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE correction_plans SET items = CAST(:items AS JSONB), updated_at = now() "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "id": plan_id,
                    "items": json.dumps([item.as_json() for item in items]),
                },
            )


__all__ = [
    "PostgresAuditScheduleStore",
    "PostgresCorrectionPlanStore",
    "PostgresVoiceBaselineStore",
]
