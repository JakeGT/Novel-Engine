"""Summaries of whole arcs and beats, and the passage index's lag across every story,
against PostgreSQL (CTX-006, CTX-005)."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from backend.adapters.part_summary_store import PostgresPartSummaryStore
from backend.adapters.passage_store import PostgresPassageQueue
from backend.core.database import create_engine, create_session_factory
from backend.domain.context import PartSummary, SummaryOrigin, SummaryState, basis_digest

pytestmark = pytest.mark.integration

WS, PR = "ws_parts_it", "pr_parts_it"


@pytest_asyncio.fixture
async def factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


def _part(
    summary_id: str,
    *,
    state: SummaryState = SummaryState.ACCEPTED,
    branch: str | None = None,
    body: str = "Mara comes home.",
) -> PartSummary:
    basis = (("scn_1", "sum_1"), ("scn_2", "sum_2"))
    return PartSummary(
        id=summary_id,
        plan_id="pln_beat",
        body=body,
        state=state,
        origin=SummaryOrigin.AUTHOR if state is SummaryState.ACCEPTED else SummaryOrigin.MODEL,
        basis=basis,
        basis_digest=basis_digest(basis),
        created_at=datetime.now(UTC),
        branch_id=branch,
    )


@pytest.mark.asyncio
async def test_one_part_summary_is_kept_per_line_and_a_suggestion_is_decided_once(
    factory,
) -> None:
    store = PostgresPartSummaryStore(factory)
    await store.add(workspace_id=WS, project_id=PR, summary=_part("psum_a"))
    await store.add(workspace_id=WS, project_id=PR, summary=_part("psum_w", branch="br_1"))
    await store.add(workspace_id=WS, project_id=PR, summary=_part("psum_b", body="Again."))
    rows = await store.for_plans(workspace_id=WS, project_id=PR, plan_ids=["pln_beat"])
    kept = {(row.id, row.branch_id) for row in rows if row.state is SummaryState.ACCEPTED}
    # A new one in the main story replaces the main story's only; the what-if's stays.
    assert kept == {("psum_w", "br_1"), ("psum_b", None)}
    (read,) = [row for row in rows if row.id == "psum_b"]
    assert read.basis == (("scn_1", "sum_1"), ("scn_2", "sum_2"))
    assert read.basis_digest == basis_digest(read.basis)

    await store.add(
        workspace_id=WS,
        project_id=PR,
        summary=_part("psum_s", state=SummaryState.PROPOSED, body="Suggested."),
    )
    decided = await store.decide(
        workspace_id=WS,
        project_id=PR,
        summary_id="psum_s",
        state=SummaryState.ACCEPTED,
        body="Corrected.",
        origin=SummaryOrigin.MODEL_CORRECTED,
        decided_by="usr",
        decided_at=datetime.now(UTC),
    )
    assert decided is not None and decided.body == "Corrected."
    assert decided.origin is SummaryOrigin.MODEL_CORRECTED
    again = await store.decide(
        workspace_id=WS,
        project_id=PR,
        summary_id="psum_s",
        state=SummaryState.REJECTED,
        body=None,
        origin=None,
        decided_by="usr",
        decided_at=datetime.now(UTC),
    )
    assert again is None
    replaced = await store.get(workspace_id=WS, project_id=PR, summary_id="psum_b")
    assert replaced is not None and replaced.state is SummaryState.REPLACED


@pytest.mark.asyncio
async def test_two_keeps_of_one_part_at_once_leave_one_kept(factory) -> None:
    # Two suggestions of the same part accepted at the same moment, and a keep written at
    # the same moment: one is kept, the others replaced; and the database refuses a second
    # kept row however it gets there.
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from backend.core.database import session_scope

    store = PostgresPartSummaryStore(factory)
    for name in ("psum_x", "psum_y"):
        await store.add(
            workspace_id=WS,
            project_id=PR,
            summary=_part(name, state=SummaryState.PROPOSED, branch="br_race"),
        )

    async def accept(summary_id: str):
        return await store.decide(
            workspace_id=WS,
            project_id=PR,
            summary_id=summary_id,
            state=SummaryState.ACCEPTED,
            body=None,
            origin=None,
            decided_by="usr",
            decided_at=datetime.now(UTC),
        )

    await asyncio.gather(
        accept("psum_x"),
        accept("psum_y"),
        store.add(workspace_id=WS, project_id=PR, summary=_part("psum_z", branch="br_race")),
    )
    rows = await store.for_plans(workspace_id=WS, project_id=PR, plan_ids=["pln_beat"])
    kept = [row.id for row in rows if row.state is SummaryState.ACCEPTED and row.branch_id]
    assert len(kept) == 1

    with pytest.raises(IntegrityError):
        async with session_scope(factory) as db:
            await db.execute(
                text(
                    "UPDATE part_summaries SET state = 'accepted' WHERE workspace_id = :w "
                    "AND project_id = :p AND branch_id = 'br_race'"
                ),
                {"w": WS, "p": PR},
            )


@pytest.mark.asyncio
async def test_the_passage_lag_counts_every_story(factory) -> None:
    queue = PostgresPassageQueue(factory)
    before = await queue.lag()
    await queue.request(workspace_id=WS, project_id=PR, source_id="src_a")
    await queue.request(workspace_id="ws_other_it", project_id="pr_other_it", source_id="src_b")
    lag = await queue.lag()
    assert lag.count == before.count + 2
    assert lag.oldest_seconds is not None and lag.oldest_seconds >= 0
