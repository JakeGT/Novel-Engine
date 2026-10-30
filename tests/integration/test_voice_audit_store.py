"""The author's usual voice, audit schedules and correction plans in PostgreSQL
(CON-018, CON-011; migration 0055): one usual per story, one schedule per part of a line
claimed once by one server, and plans kept with each step's decision."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio

from backend.adapters.voice_audit_store import (
    PostgresAuditScheduleStore,
    PostgresCorrectionPlanStore,
    PostgresVoiceBaselineStore,
)
from backend.core.database import create_engine, create_session_factory
from backend.domain.audit_plans import AuditSchedule, CorrectionItem, CorrectionPlan
from backend.domain.identity import utcnow

pytestmark = pytest.mark.integration

WS, PR = "ws_voice_it", "pr_voice_it"


@pytest_asyncio.fixture
async def factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_scenes_left_out_of_the_usual_are_kept_per_story(factory) -> None:
    store = PostgresVoiceBaselineStore(factory)
    assert await store.left_out(workspace_id=WS, project_id=PR) == []
    await store.set_left_out(
        workspace_id=WS, project_id=PR, plan_ids=["s1", "s2"], actor_user_id="u"
    )
    await store.set_left_out(workspace_id=WS, project_id=PR, plan_ids=["s2"], actor_user_id="u")
    assert await store.left_out(workspace_id=WS, project_id=PR) == ["s2"]
    assert await store.left_out(workspace_id=WS, project_id="other") == []


def _schedule(
    id_: str, plan_id: str = "b1", branch_id: str | None = None, **extra
) -> AuditSchedule:
    return AuditSchedule(
        workspace_id=WS,
        project_id=PR,
        id=id_,
        branch_id=branch_id,
        plan_id=plan_id,
        passes=("clarity",),
        every_days=extra.pop("every_days", 7),
        max_tokens=extra.pop("max_tokens", 20_000),
        actor_user_id="usr_1",
        auth_mode="local",
        next_run_at=extra.pop("next_run_at", utcnow() - timedelta(minutes=1)),
        **extra,
    )


@pytest.mark.asyncio
async def test_one_schedule_per_part_of_a_line_and_a_due_run_is_claimed_once(factory) -> None:
    store = PostgresAuditScheduleStore(factory)
    first = await store.save(_schedule("sch_1"))
    # The same part of the same line: replaced, keeping its id.
    again = await store.save(_schedule("sch_2", every_days=3))
    assert again.id == first.id == "sch_1" and again.every_days == 3
    # The same part in a what-if is its own schedule.
    await store.save(_schedule("sch_3", branch_id="br_x"))
    assert {item.id for item in await store.all_for(workspace_id=WS, project_id=PR)} == {
        "sch_1",
        "sch_3",
    }

    now = utcnow()
    claims = await asyncio.gather(
        store.claim_due(now=now, hold_until=now + timedelta(minutes=30)),
        store.claim_due(now=now, hold_until=now + timedelta(minutes=30)),
    )
    claimed = [item.id for batch in claims for item in batch]
    assert sorted(claimed) == ["sch_1", "sch_3"]  # each once, by one of the two
    assert await store.claim_due(now=now, hold_until=now + timedelta(minutes=30)) == []

    await store.record_run(
        workspace_id=WS,
        project_id=PR,
        schedule_id="sch_1",
        ran_at=now,
        outcome="started",
        next_run_at=now + timedelta(days=3),
        job_id="job_1",
        inputs="digest",
    )
    kept = await store.get(workspace_id=WS, project_id=PR, schedule_id="sch_1")
    assert kept is not None and kept.last_outcome == "started" and kept.last_job_id == "job_1"
    assert kept.last_inputs == "digest" and kept.enabled
    await store.record_run(
        workspace_id=WS,
        project_id=PR,
        schedule_id="sch_1",
        ran_at=now,
        outcome="gone",
        next_run_at=now,
        enabled=False,
    )
    off = await store.get(workspace_id=WS, project_id=PR, schedule_id="sch_1")
    assert off is not None and not off.enabled and off.last_job_id == "job_1"
    later = await store.claim_due(now=now + timedelta(days=9), hold_until=now)
    assert all(item.id != "sch_1" for item in later)  # turned off: never claimed
    assert await store.delete(workspace_id=WS, project_id=PR, schedule_id="sch_3")
    assert not await store.delete(workspace_id=WS, project_id=PR, schedule_id="sch_3")


@pytest.mark.asyncio
async def test_a_correction_plan_keeps_each_steps_decision(factory) -> None:
    store = PostgresCorrectionPlanStore(factory)
    item = CorrectionItem(
        plan_id="s1",
        title="One",
        change="Drop the gulls.",
        why="Seen too often.",
        findings=("note:fnd_1",),
        cited=("Gulls again.",),
    )
    plan = await store.add(
        CorrectionPlan(
            workspace_id=WS,
            project_id=PR,
            id="cpl_1",
            branch_id=None,
            audit_job_id="job_a",
            job_id="job_c",
            summary="Cut the gulls back.",
            items=(item,),
            model="fake",
            prompt_version="plan-corrections/1",
            dropped=2,
            created_by="usr_1",
        )
    )
    assert plan.items == (item,) and plan.dropped == 2
    assert (await store.for_job(workspace_id=WS, project_id=PR, job_id="job_c")).id == "cpl_1"  # type: ignore[union-attr]
    from dataclasses import replace

    await store.set_items(
        workspace_id=WS, project_id=PR, plan_id="cpl_1", items=[replace(item, decision="kept")]
    )
    (found,) = await store.for_audit(workspace_id=WS, project_id=PR, audit_job_id="job_a")
    assert found.items[0].decision == "kept" and found.items[0].cited == ("Gulls again.",)


@pytest.mark.asyncio
async def test_decisions_on_two_steps_made_at_once_both_stand(factory) -> None:
    store = PostgresCorrectionPlanStore(factory)
    steps = tuple(
        CorrectionItem(
            plan_id=f"s{n}", title=f"Scene {n}", change="Cut it.", why="Too long.", findings=("c",)
        )
        for n in range(6)
    )
    await store.add(
        CorrectionPlan(
            workspace_id=WS,
            project_id=PR,
            id="cpl_race",
            branch_id=None,
            audit_job_id="job_a",
            job_id="job_race",
            summary="",
            items=steps,
            model="fake",
            prompt_version="plan-corrections/1",
            dropped=0,
            created_by="usr_1",
        )
    )
    # Read-then-write of the whole list loses all but one of these.
    await asyncio.gather(
        *(
            store.set_decision(
                workspace_id=WS,
                project_id=PR,
                plan_id="cpl_race",
                index=n,
                decision="kept" if n % 2 else "skipped",
            )
            for n in range(6)
        )
    )
    found = await store.get(workspace_id=WS, project_id=PR, plan_id="cpl_race")
    assert found is not None
    assert [item.decision for item in found.items] == ["skipped", "kept"] * 3
    assert [item.plan_id for item in found.items] == [f"s{n}" for n in range(6)]
    for index in (6, -1):
        assert (
            await store.set_decision(
                workspace_id=WS, project_id=PR, plan_id="cpl_race", index=index, decision="kept"
            )
            is None
        )
    assert (
        await store.set_decision(
            workspace_id=WS, project_id=PR, plan_id="cpl_none", index=0, decision="kept"
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_what_ifs_schedules_go_with_it_and_no_others(factory) -> None:
    store = PostgresAuditScheduleStore(factory)
    await store.save(_schedule("sch_main"))
    await store.save(_schedule("sch_x1", branch_id="br_x"))
    await store.save(_schedule("sch_x2", plan_id="b2", branch_id="br_x"))
    await store.save(_schedule("sch_y", branch_id="br_y"))
    assert await store.delete_for_branch(workspace_id=WS, project_id=PR, branch_id="br_x") == 2
    left = {item.id for item in await store.all_for(workspace_id=WS, project_id=PR)}
    assert left == {"sch_main", "sch_y"}
    assert await store.delete_for_branch(workspace_id=WS, project_id=PR, branch_id="br_x") == 0
