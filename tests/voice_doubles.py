"""In-memory stores for the author's usual voice, audit schedules and correction plans
(CON-018, CON-011), standing in for ``backend.adapters.voice_audit_store``."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from backend.domain.audit_plans import AuditSchedule, CorrectionItem, CorrectionPlan
from backend.domain.identity import utcnow


class InMemoryVoiceBaselineStore:
    def __init__(self) -> None:
        self.left: dict[tuple[str, str], list[str]] = {}

    async def left_out(self, *, workspace_id: str, project_id: str) -> list[str]:
        return list(self.left.get((workspace_id, project_id), []))

    async def set_left_out(
        self, *, workspace_id: str, project_id: str, plan_ids: list[str], actor_user_id: str | None
    ) -> None:
        self.left[(workspace_id, project_id)] = list(plan_ids)


class InMemoryAuditScheduleStore:
    def __init__(self) -> None:
        self.schedules: dict[tuple[str, str, str], AuditSchedule] = {}

    async def all_for(self, *, workspace_id: str, project_id: str) -> list[AuditSchedule]:
        return [
            item
            for (w, p, _), item in self.schedules.items()
            if (w, p) == (workspace_id, project_id)
        ]

    async def get(
        self, *, workspace_id: str, project_id: str, schedule_id: str
    ) -> AuditSchedule | None:
        return self.schedules.get((workspace_id, project_id, schedule_id))

    async def save(self, schedule: AuditSchedule) -> AuditSchedule:
        now = utcnow()
        for key, item in list(self.schedules.items()):
            if (item.workspace_id, item.project_id, item.branch_id or "", item.plan_id) == (
                schedule.workspace_id,
                schedule.project_id,
                schedule.branch_id or "",
                schedule.plan_id,
            ):
                kept = replace(
                    item,
                    passes=schedule.passes,
                    every_days=schedule.every_days,
                    max_tokens=schedule.max_tokens,
                    actor_user_id=schedule.actor_user_id,
                    auth_mode=schedule.auth_mode,
                    enabled=schedule.enabled,
                    next_run_at=schedule.next_run_at,
                    updated_at=now,
                )
                self.schedules[key] = kept
                return kept
        made = replace(schedule, created_at=now, updated_at=now)
        self.schedules[(schedule.workspace_id, schedule.project_id, schedule.id)] = made
        return made

    async def delete(self, *, workspace_id: str, project_id: str, schedule_id: str) -> bool:
        return self.schedules.pop((workspace_id, project_id, schedule_id), None) is not None

    async def delete_for_branch(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        gone = [
            key
            for key, item in self.schedules.items()
            if (item.workspace_id, item.project_id, item.branch_id)
            == (workspace_id, project_id, branch_id)
        ]
        for key in gone:
            del self.schedules[key]
        return len(gone)

    async def claim_due(
        self, *, now: datetime, hold_until: datetime, limit: int = 20
    ) -> list[AuditSchedule]:
        due = sorted(
            (item for item in self.schedules.values() if item.enabled and item.next_run_at <= now),
            key=lambda item: item.next_run_at,
        )[:limit]
        claimed = []
        for item in due:
            held = replace(item, next_run_at=hold_until)
            self.schedules[(item.workspace_id, item.project_id, item.id)] = held
            claimed.append(held)
        return claimed

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
        key = (workspace_id, project_id, schedule_id)
        item = self.schedules.get(key)
        if item is None:
            return
        self.schedules[key] = replace(
            item,
            last_run_at=ran_at,
            last_outcome=outcome,
            next_run_at=next_run_at,
            enabled=enabled,
            last_job_id=job_id or item.last_job_id,
            last_inputs=inputs or item.last_inputs,
            updated_at=utcnow(),
        )


class InMemoryCorrectionPlanStore:
    def __init__(self) -> None:
        self.plans: dict[tuple[str, str, str], CorrectionPlan] = {}

    async def add(self, plan: CorrectionPlan) -> CorrectionPlan:
        key = (plan.workspace_id, plan.project_id, plan.id)
        if key not in self.plans:
            now = utcnow()
            self.plans[key] = replace(plan, created_at=now, updated_at=now)
        return self.plans[key]

    async def get(
        self, *, workspace_id: str, project_id: str, plan_id: str
    ) -> CorrectionPlan | None:
        return self.plans.get((workspace_id, project_id, plan_id))

    async def for_job(
        self, *, workspace_id: str, project_id: str, job_id: str
    ) -> CorrectionPlan | None:
        return next(
            (
                plan
                for plan in self.plans.values()
                if (plan.workspace_id, plan.project_id, plan.job_id)
                == (workspace_id, project_id, job_id)
            ),
            None,
        )

    async def for_audit(
        self, *, workspace_id: str, project_id: str, audit_job_id: str
    ) -> list[CorrectionPlan]:
        found = [
            plan
            for plan in self.plans.values()
            if (plan.workspace_id, plan.project_id, plan.audit_job_id)
            == (workspace_id, project_id, audit_job_id)
        ]
        return sorted(found, key=lambda plan: (plan.created_at or utcnow(), plan.id), reverse=True)

    async def set_decision(
        self, *, workspace_id: str, project_id: str, plan_id: str, index: int, decision: str
    ) -> CorrectionPlan | None:
        key = (workspace_id, project_id, plan_id)
        plan = self.plans.get(key)
        if plan is None or not 0 <= index < len(plan.items):
            return None
        items = list(plan.items)
        items[index] = replace(items[index], decision=decision)  # type: ignore[arg-type]
        self.plans[key] = replace(plan, items=tuple(items), updated_at=utcnow())
        return self.plans[key]

    async def set_items(
        self, *, workspace_id: str, project_id: str, plan_id: str, items: list[CorrectionItem]
    ) -> None:
        key = (workspace_id, project_id, plan_id)
        if key in self.plans:
            self.plans[key] = replace(self.plans[key], items=tuple(items), updated_at=utcnow())


__all__ = [
    "InMemoryAuditScheduleStore",
    "InMemoryCorrectionPlanStore",
    "InMemoryVoiceBaselineStore",
]
