"""Checks kept as runs, in PostgreSQL (CON-001, migration 0044): one run per job, a scene's
history includes the audits that read it, and runs belong to their line of the story."""

from __future__ import annotations

import pytest
import pytest_asyncio

from backend.adapters.consistency_run_store import PostgresConsistencyRunStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.consistency_runs import ConsistencyRun, RecordedCheck, RunInput

pytestmark = pytest.mark.integration

WS, PR = "ws_runs_it", "pr_runs_it"


@pytest_asyncio.fixture
async def store(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresConsistencyRunStore(create_session_factory(engine))
    finally:
        await engine.dispose()


def _run(id_: str, kind: str, inputs: list[str], **extra) -> ConsistencyRun:
    return ConsistencyRun(
        workspace_id=WS,
        project_id=PR,
        id=id_,
        branch_id=extra.pop("branch_id", "main"),
        kind=kind,  # type: ignore[arg-type]
        scope=extra.pop("scope", "scene"),
        plan_id=inputs[0] if inputs else None,
        story_revision=extra.pop("story_revision", 4),
        inputs=tuple(RunInput(plan_id=p, source_id="t", version_id=f"v-{p}") for p in inputs),
        passes=("model_free",),
        **extra,
    )


@pytest.mark.asyncio
async def test_runs_are_kept_once_per_job_and_found_by_scene_line_and_job(store) -> None:
    check = RecordedCheck("s1", "stock_phrase", "note", "“palpable” is stock.", 3, 11, "palpable")
    assert await store.add(_run("r1", "checks", ["s1"], findings=(check,)))
    assert await store.add(_run("r2", "audit", ["s1", "s2"], scope="part", job_id="job-1"))
    # The same job again (a worker taking over) keeps no second run.
    assert not await store.add(_run("r3", "audit", ["s1", "s2"], scope="part", job_id="job-1"))
    assert await store.add(_run("r4", "checks", ["s1"], branch_id="what-if"))

    first = await store.get(workspace_id=WS, project_id=PR, run_id="r1")
    assert first is not None and first.findings == (check,)
    assert first.inputs[0].version_id == "v-s1" and first.story_revision == 4
    assert first.created_at is not None

    in_s2 = await store.recent(workspace_id=WS, project_id=PR, branch_id="main", plan_id="s2")
    assert [run.id for run in in_s2] == ["r2"]
    in_s1 = await store.recent(workspace_id=WS, project_id=PR, branch_id="main", plan_id="s1")
    assert {run.id for run in in_s1} == {"r1", "r2"}
    only_checks = await store.recent(
        workspace_id=WS, project_id=PR, branch_id="main", plan_id="s1", kind="checks", limit=1
    )
    assert [run.id for run in only_checks] == ["r1"]
    elsewhere = await store.recent(workspace_id=WS, project_id=PR, branch_id="what-if")
    assert [run.id for run in elsewhere] == ["r4"]
    by_job = await store.for_job(workspace_id=WS, project_id=PR, job_id="job-1")
    assert by_job is not None and by_job.id == "r2"
    assert await store.get(workspace_id=WS, project_id="other", run_id="r1") is None
