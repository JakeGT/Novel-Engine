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


@pytest.mark.asyncio
async def test_a_run_copied_into_a_what_if_keeps_its_id_and_its_fingerprints(store) -> None:
    kept = {"scenes": {"s1": {"inputs": {"prose": "a" * 64}, "checks": {"stock": "b" * 64}}}}
    profile = {"checks": "scene-checks/1", "checking": "on_request"}
    assert await store.add(_run("r9", "checks", ["s1"], fingerprints=kept, profile=profile))
    # The same run in another line of the story (a what-if forked from this one).
    assert await store.add(_run("r9", "checks", ["s1"], branch_id="w2", fingerprints=kept))
    main = await store.get(workspace_id=WS, project_id=PR, run_id="r9", branch_id="main")
    other = await store.get(workspace_id=WS, project_id=PR, run_id="r9", branch_id="w2")
    assert main is not None and other is not None and main.branch_id != other.branch_id
    assert main.fingerprints == kept and main.profile == profile
    assert main.scene_input("s1", "prose") == "a" * 64
    assert [
        run.id for run in await store.all_in(workspace_id=WS, project_id=PR, branch_id="w2")
    ] == ["r9"]


@pytest.mark.asyncio
async def test_the_check_cache_keeps_the_newest_results_of_each_check(store) -> None:
    found = [RecordedCheck("s1", "stock_phrase", "note", "stock", 1, 4, "x")]
    for number in range(4):
        await store.keep_cached(
            workspace_id=WS,
            project_id=PR,
            plan_id="s1",
            entries=[{"check": "stock", "fingerprint": f"{number:064d}", "findings": found}],
            keep=2,
        )
    got = await store.cached(
        workspace_id=WS,
        project_id=PR,
        plan_id="s1",
        keys=[("stock", f"{number:064d}") for number in range(4)] + [("names", "0" * 64)],
    )
    assert set(got) == {("stock", f"{2:064d}"), ("stock", f"{3:064d}")}
    assert got[("stock", f"{3:064d}")]["findings"] == found


@pytest.mark.asyncio
async def test_review_dismissals_and_what_a_dismissal_depends_on(disposable_database) -> None:
    from datetime import UTC, datetime

    from backend.adapters.review_dismissal_store import PostgresReviewDismissalStore
    from backend.adapters.studio_store import PostgresStudioStore
    from backend.domain.dismissals import (
        DismissReason,
        FindingDismissal,
        FindingKind,
        ReviewDismissal,
    )

    engine = create_engine(disposable_database.settings())
    try:
        factory = create_session_factory(engine)
        reviews = PostgresReviewDismissalStore(factory)
        now = datetime.now(UTC)
        first = ReviewDismissal(
            WS,
            PR,
            "d1",
            "m1",
            "f" * 64,
            "a" * 64,
            "possible_duplicate",
            DismissReason.NOT_A_PROBLEM,
            "",
            None,
            now,
        )
        await reviews.add(first)
        # Dismissing the same finding again replaces the standing dismissal.
        await reviews.add(
            ReviewDismissal(
                WS,
                PR,
                "d2",
                "m1",
                "f" * 64,
                "b" * 64,
                "possible_duplicate",
                DismissReason.INTENDED,
                "",
                None,
                now,
            )
        )
        kept = await reviews.for_mutation(workspace_id=WS, project_id=PR, mutation_id="m1")
        assert [(item.id, item.standing) for item in kept] == [("d1", False), ("d2", True)]
        assert await reviews.withdraw(
            workspace_id=WS, project_id=PR, mutation_id="m1", dismissal_id="d2", at=now
        )
        assert not await reviews.withdraw(
            workspace_id=WS, project_id=PR, mutation_id="m1", dismissal_id="d2", at=now
        )

        studio = PostgresStudioStore(factory)
        await studio.add_dismissal(
            FindingDismissal(
                WS,
                PR,
                "main",
                "sd1",
                "s1",
                "c" * 64,
                FindingKind.CHECK,
                "secret",
                DismissReason.NOT_A_PROBLEM,
                "",
                None,
                None,
                now,
                depends_on="d" * 64,
            )
        )
        (back,) = await studio.dismissals(
            workspace_id=WS, project_id=PR, branch_id="main", plan_id="s1"
        )
        assert back.depends_on == "d" * 64
    finally:
        await engine.dispose()
