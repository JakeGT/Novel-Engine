"""The passage index's waiting list, scene summaries and search filters against
PostgreSQL (CTX-005, CTX-006, CTX-004)."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.adapters.embedding_generations import PgEmbeddingIndexStore
from backend.adapters.passage_store import PostgresPassageQueue, PostgresSceneSummaryStore
from backend.adapters.vector import PgVectorStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.context import SceneSummary, SummaryOrigin, SummaryState

pytestmark = pytest.mark.integration

WS, PR = "ws_layers_it", "pr_layers_it"


@pytest_asyncio.fixture
async def factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_waiting_list_coalesces_claims_once_and_keeps_a_newer_change(factory) -> None:
    queue = PostgresPassageQueue(factory)
    await queue.request(workspace_id=WS, project_id=PR, source_id="src_a")
    await queue.request(workspace_id=WS, project_id=PR, source_id="src_b")
    waiting = await queue.waiting(workspace_id=WS, project_id=PR)
    assert waiting.count == 2 and waiting.oldest_seconds is not None and waiting.failing == 0
    assert (WS, PR) in await queue.stories_waiting(limit=10)

    claimed = await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60)
    assert [item.source_id for item in claimed] == ["src_a", "src_b"]
    # Claimed: nobody else gets them while the lease lasts.
    assert await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60) == []
    assert (WS, PR) not in await queue.stories_waiting(limit=10)

    # A change while src_a is being read: its row stays, ready again once let go.
    await queue.request(workspace_id=WS, project_id=PR, source_id="src_a")
    first, second = claimed
    assert not await queue.done(
        workspace_id=WS, project_id=PR, source_id="src_a", generation=first.generation
    )
    assert await queue.done(
        workspace_id=WS, project_id=PR, source_id="src_b", generation=second.generation
    )
    (again,) = await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60)
    assert again.source_id == "src_a" and again.generation == first.generation + 1

    assert await queue.failed(
        workspace_id=WS,
        project_id=PR,
        source_id="src_a",
        generation=again.generation,
        error="Boom",
        retry_after_seconds=0,
    )
    waiting = await queue.waiting(workspace_id=WS, project_id=PR)
    assert waiting.count == 1 and waiting.failing == 1
    (retry,) = await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60)
    assert retry.attempts == 1
    assert await queue.done(
        workspace_id=WS, project_id=PR, source_id="src_a", generation=retry.generation
    )
    assert (await queue.waiting(workspace_id=WS, project_id=PR)).count == 0


@pytest.mark.asyncio
async def test_a_newer_change_is_never_held_back_by_an_older_failure(factory) -> None:
    queue = PostgresPassageQueue(factory)
    await queue.request(workspace_id=WS, project_id=PR, source_id="src_c")
    (first,) = await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60)
    # A change while the old version is being read, and then that read fails.
    await queue.request(workspace_id=WS, project_id=PR, source_id="src_c")
    assert not await queue.failed(
        workspace_id=WS,
        project_id=PR,
        source_id="src_c",
        generation=first.generation,
        error="Boom",
        retry_after_seconds=600,
    )
    (second,) = await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60)
    assert second.generation == first.generation + 1 and second.attempts == 0
    # That one fails with a long wait; a fresh change is read at once all the same.
    assert await queue.failed(
        workspace_id=WS,
        project_id=PR,
        source_id="src_c",
        generation=second.generation,
        error="Boom",
        retry_after_seconds=600,
    )
    assert await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60) == []
    await queue.request(workspace_id=WS, project_id=PR, source_id="src_c")
    (fresh,) = await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60)
    assert fresh.attempts == 0
    # A text being read keeps its claim when it changes meanwhile.
    await queue.request(workspace_id=WS, project_id=PR, source_id="src_c")
    assert await queue.claim(workspace_id=WS, project_id=PR, limit=10, lease_seconds=60) == []


def _summary(summary_id: str, state: SummaryState, version: str = "srv_1") -> SceneSummary:
    return SceneSummary(
        id=summary_id,
        plan_id="pln_1",
        source_id="src_scene",
        version_id=version,
        body=f"Summary {summary_id}.",
        state=state,
        origin=SummaryOrigin.MODEL if state is SummaryState.PROPOSED else SummaryOrigin.AUTHOR,
        created_at=datetime.now(UTC),
        model="stand-in",
        prompt_version="scene-summary/1",
        job_id="job_1",
    )


@pytest.mark.asyncio
async def test_one_summary_is_kept_per_text_and_a_suggestion_is_decided_once(factory) -> None:
    store = PostgresSceneSummaryStore(factory)
    await store.add(
        workspace_id=WS, project_id=PR, summary=_summary("sum_a", SummaryState.ACCEPTED)
    )
    await store.add(
        workspace_id=WS, project_id=PR, summary=_summary("sum_b", SummaryState.PROPOSED)
    )
    rows = await store.for_sources(workspace_id=WS, project_id=PR, source_ids=["src_scene"])
    assert [(row.id, row.state) for row in rows] == [
        ("sum_a", SummaryState.ACCEPTED),
        ("sum_b", SummaryState.PROPOSED),
    ]
    kept = await store.decide(
        workspace_id=WS,
        project_id=PR,
        summary_id="sum_b",
        state=SummaryState.ACCEPTED,
        body="Corrected.",
        origin=SummaryOrigin.MODEL_CORRECTED,
        decided_by="usr_1",
        decided_at=datetime.now(UTC),
    )
    assert kept is not None and kept.body == "Corrected." and kept.model == "stand-in"
    assert kept.origin is SummaryOrigin.MODEL_CORRECTED
    # The one kept before is replaced, not listed.
    rows = await store.for_sources(workspace_id=WS, project_id=PR, source_ids=["src_scene"])
    assert [row.id for row in rows] == ["sum_b"]
    assert (await store.get(workspace_id=WS, project_id=PR, summary_id="sum_a")).state is (
        SummaryState.REPLACED
    )
    again = await store.decide(
        workspace_id=WS,
        project_id=PR,
        summary_id="sum_b",
        state=SummaryState.REJECTED,
        body=None,
        origin=None,
        decided_by="usr_1",
        decided_at=datetime.now(UTC),
    )
    assert again is None
    assert (
        await store.for_sources(workspace_id="other", project_id=PR, source_ids=["src_scene"]) == []
    )


@pytest.mark.asyncio
async def test_a_summary_kept_in_a_what_if_never_replaces_the_main_storys(factory) -> None:
    from dataclasses import replace

    store = PostgresSceneSummaryStore(factory)
    main = _summary("sum_main", SummaryState.ACCEPTED)
    await store.add(workspace_id=WS, project_id=PR, summary=main)
    what_if = replace(_summary("sum_wi", SummaryState.ACCEPTED), branch_id="br_what_if")
    await store.add(workspace_id=WS, project_id=PR, summary=what_if)
    rows = await store.for_sources(workspace_id=WS, project_id=PR, source_ids=["src_scene"])
    assert {(row.id, row.branch_id) for row in rows} == {
        ("sum_main", None),
        ("sum_wi", "br_what_if"),
    }
    # A newer one in the main story replaces only the main story's.
    await store.add(
        workspace_id=WS, project_id=PR, summary=_summary("sum_main2", SummaryState.ACCEPTED)
    )
    rows = await store.for_sources(workspace_id=WS, project_id=PR, source_ids=["src_scene"])
    assert {row.id for row in rows} == {"sum_main2", "sum_wi"}
    # And a suggestion kept in the what-if replaces only the what-if's.
    proposed = replace(_summary("sum_wi2", SummaryState.PROPOSED), branch_id="br_what_if")
    await store.add(workspace_id=WS, project_id=PR, summary=proposed)
    kept = await store.decide(
        workspace_id=WS,
        project_id=PR,
        summary_id="sum_wi2",
        state=SummaryState.ACCEPTED,
        body=None,
        origin=None,
        decided_by="usr_1",
        decided_at=datetime.now(UTC),
    )
    assert kept is not None and kept.branch_id == "br_what_if"
    rows = await store.for_sources(workspace_id=WS, project_id=PR, source_ids=["src_scene"])
    assert {row.id for row in rows} == {"sum_main2", "sum_wi2"}


def test_search_filters_by_metadata_in_the_same_scan(disposable_database) -> None:  # type: ignore[no-untyped-def]
    dsn = disposable_database.psycopg_dsn
    store = PgVectorStore(
        dsn, DeterministicEmbedding(dimension=64), index=PgEmbeddingIndexStore(dsn)
    )
    store.add_documents(
        ["the basalt coast", "the basalt coast again"],
        [
            {"kind": "passage", "source_id": "src_main", "source_kind": "scene"},
            {"kind": "passage", "source_id": "src_other", "source_kind": "paste"},
        ],
        ["passage:src_main:1", "passage:src_other:1"],
        workspace_id=WS,
        project_id=PR,
    )
    every = store.query("basalt coast", workspace_id=WS, project_id=PR, n_results=5)
    assert {hit["id"] for hit in every} == {"passage:src_main:1", "passage:src_other:1"}
    only = store.query(
        "basalt coast",
        workspace_id=WS,
        project_id=PR,
        n_results=5,
        where={"source_id": ["src_main"]},
    )
    assert [hit["id"] for hit in only] == ["passage:src_main:1"]
    pasted = store.query(
        "basalt coast", workspace_id=WS, project_id=PR, where={"source_kind": ["paste"]}
    )
    assert [hit["id"] for hit in pasted] == ["passage:src_other:1"]


@pytest.mark.asyncio
async def test_a_claim_for_one_text_holds_exactly_one(factory) -> None:
    # As a plain subquery the pick could run again inside the update and hold more
    # texts than asked for, which the reader then never let go until the lease ended.
    queue = PostgresPassageQueue(factory)
    for source in ("src_c", "src_d", "src_e"):
        await queue.request(workspace_id=WS, project_id=PR, source_id=source)
    seen: list[str] = []
    for _ in range(3):
        claimed = await queue.claim(workspace_id=WS, project_id=PR, limit=1, lease_seconds=60)
        assert len(claimed) == 1
        seen.append(claimed[0].source_id)
        assert await queue.done(
            workspace_id=WS,
            project_id=PR,
            source_id=claimed[0].source_id,
            generation=claimed[0].generation,
        )
    assert seen == ["src_c", "src_d", "src_e"]
    assert (await queue.waiting(workspace_id=WS, project_id=PR)).count == 0
