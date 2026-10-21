"""One look for due schedules does a bounded amount, so waiting jobs are not held up
(CON-011)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.domain.audit_plans import AuditSchedule
from backend.domain.identity import utcnow
from backend.services.audit_schedules import CLAIM_LIMIT, AuditScheduleService
from tests.voice_doubles import InMemoryAuditScheduleStore

pytestmark = pytest.mark.unit


def test_one_look_starts_at_most_a_few_schedules_and_the_rest_wait_for_the_next() -> None:
    store = InMemoryAuditScheduleStore()
    now = utcnow()
    for n in range(CLAIM_LIMIT + 3):
        store.schedules[("ws", "pr", f"sch_{n}")] = AuditSchedule(
            workspace_id="ws",
            project_id="pr",
            id=f"sch_{n}",
            branch_id=None,
            plan_id=f"b{n}",
            passes=(),
            every_days=7,
            max_tokens=0,
            actor_user_id="usr",
            auth_mode="local",
            next_run_at=now - timedelta(minutes=n + 1),
        )
    service = AuditScheduleService(audits=SimpleNamespace(), store=store, clock=lambda: now)
    ran: list[str] = []

    async def run(schedule, *, now=None):
        ran.append(schedule.id)
        return "unchanged"

    service.run = run  # type: ignore[method-assign]
    assert asyncio.run(service.tick()) == CLAIM_LIMIT
    assert asyncio.run(service.tick()) == 3
    assert len(set(ran)) == CLAIM_LIMIT + 3
