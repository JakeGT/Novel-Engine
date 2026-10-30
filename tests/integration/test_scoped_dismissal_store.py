"""Dismissals wider than one scene, and review findings seen (CON-016), in PostgreSQL:
kept, listed, withdrawn with the record kept, forked with a what-if, deleted with the
project."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.review_dismissal_store import PostgresReviewDismissalStore
from backend.adapters.scoped_dismissal_store import PostgresScopedDismissalStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.dismissals import DismissalScope, DismissReason, FindingKind, ScopedDismissal
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEntityOp
from backend.services.mutation_service import MutationService

pytestmark = pytest.mark.integration

WS, PR = "ws_scoped_it", "pr_scoped_it"
SCOPE = AccessScope(
    actor_user_id="usr_author", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
)


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    repository = PostgresCanonRepository(factory)
    service = MutationService(PostgresCanonJournal(factory), repository, local_mode=True)
    try:
        yield service, repository, factory
    finally:
        await engine.dispose()


async def _root(service, factory) -> str:
    draft = await service.create_draft(
        SCOPE,
        workspace_id=WS,
        project_id=PR,
        operations=[CreateEntityOp(id="tmp:m", type="Character", name="Mara")],
        expected_head=0,
    )
    await service.commit(SCOPE, draft.id)
    async with session_scope(factory) as db:
        return str(
            (
                await db.execute(
                    text("SELECT id FROM branches WHERE workspace_id = :w AND project_id = :p"),
                    {"w": WS, "p": PR},
                )
            ).scalar_one()
        )


@pytest.mark.asyncio
async def test_a_wider_dismissal_is_kept_withdrawn_and_forked(stack) -> None:
    service, repository, factory = stack
    root = await _root(service, factory)
    store = PostgresScopedDismissalStore(factory)
    now = datetime.now(UTC)
    await store.add(
        ScopedDismissal(
            workspace_id=WS,
            project_id=PR,
            branch_id=root,
            id="sd1",
            kind=FindingKind.CHECK,
            code="appearance_differs",
            scope=DismissalScope.CHARACTER,
            reason=DismissReason.NOT_A_PROBLEM,
            note="",
            created_at=now,
            entity_id="ent_mara",
        )
    )
    await store.add(
        ScopedDismissal(
            workspace_id=WS,
            project_id=PR,
            branch_id=root,
            id="sd2",
            kind=FindingKind.NOTE,
            code="voice",
            scope=DismissalScope.RANGE,
            reason=DismissReason.OTHER,
            note="Her chapters are meant to be clipped.",
            created_at=now + timedelta(seconds=1),
            from_plan_id="pln_a",
            to_plan_id="pln_b",
            expires_at=now + timedelta(days=7),
        )
    )
    first, second = await store.list(workspace_id=WS, project_id=PR, branch_id=root)
    assert (first.id, first.scope, first.entity_id) == ("sd1", DismissalScope.CHARACTER, "ent_mara")
    assert (second.from_plan_id, second.to_plan_id, second.note) == (
        "pln_a",
        "pln_b",
        "Her chapters are meant to be clipped.",
    )
    assert await store.count(workspace_id=WS, project_id=PR, branch_id=root) == 2

    await repository.fork_branch(
        workspace_id=WS,
        project_id=PR,
        parent_branch_id=root,
        branch_id="brn_whatif",
        name="What if",
        description=None,
        actor_user_id="usr_author",
    )
    assert await store.withdraw(
        workspace_id=WS, project_id=PR, branch_id="brn_whatif", dismissal_id="sd1", at=now
    )
    assert not await store.withdraw(
        workspace_id=WS, project_id=PR, branch_id="brn_whatif", dismissal_id="sd1", at=now
    )
    copied = await store.list(workspace_id=WS, project_id=PR, branch_id="brn_whatif")
    assert [item.withdrawn_at is not None for item in copied] == [True, False]
    # The main story keeps its decision.
    kept = await store.list(workspace_id=WS, project_id=PR, branch_id=root)
    assert all(item.withdrawn_at is None for item in kept)

    reviews = PostgresReviewDismissalStore(factory)
    for _ in range(2):
        await reviews.see(
            workspace_id=WS,
            project_id=PR,
            mutation_id="mut_1",
            found=[("a" * 64, "possible_duplicate"), ("b" * 64, "rule:rul_1")],
        )
    assert await reviews.seen_counts(workspace_id=WS, project_id=PR) == {
        "possible_duplicate": 1,
        "rule:rul_1": 1,
    }
    assert await reviews.for_project(workspace_id=WS, project_id=PR) == []

    await repository.purge_project(workspace_id=WS, project_id=PR)
    async with session_scope(factory) as db:
        for table in ("scoped_dismissals", "review_finding_seen"):
            left = (
                await db.execute(
                    text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w"),  # noqa: S608
                    {"w": WS},
                )
            ).scalar_one()
            assert left == 0, table


@pytest.mark.asyncio
async def test_dismissals_restored_from_an_archive_keep_their_record_once(stack) -> None:
    """CON-016: a restore puts each back as it was, withdrawn or not, and only once."""
    from backend.domain.dismissals import ReviewDismissal

    service, _repository, factory = stack
    root = await _root(service, factory)
    now = datetime.now(UTC)
    scoped = PostgresScopedDismissalStore(factory)
    item = ScopedDismissal(
        workspace_id=WS,
        project_id=PR,
        branch_id=root,
        id="sd_restored",
        kind=FindingKind.CHECK,
        code="stock_phrase",
        scope=DismissalScope.RANGE,
        reason=DismissReason.INTENDED,
        note="",
        created_at=now,
        from_plan_id="pln_a",
        to_plan_id="pln_b",
        withdrawn_at=now + timedelta(seconds=5),
    )
    assert await scoped.restore(item)
    assert not await scoped.restore(item)
    (back,) = await scoped.list(workspace_id=WS, project_id=PR, branch_id=root)
    assert back.withdrawn_at is not None and back.from_plan_id == "pln_a"

    reviews = PostgresReviewDismissalStore(factory)

    def review(dismissal_id: str, gone: datetime | None) -> ReviewDismissal:
        return ReviewDismissal(
            workspace_id=WS,
            project_id=PR,
            id=dismissal_id,
            mutation_id="mut_restored",
            fingerprint="c" * 64,
            depends_on="d" * 64,
            code="possible_duplicate",
            reason=DismissReason.NOT_A_PROBLEM,
            note="",
            created_by="usr_author",
            created_at=now,
            withdrawn_at=gone,
        )

    assert await reviews.restore(review("rd_1", now))
    assert await reviews.restore(review("rd_2", None))
    # A second standing one for the same finding is left out, as is the same id again.
    assert not await reviews.restore(review("rd_3", None))
    assert not await reviews.restore(review("rd_1", now))
    found = await reviews.for_mutation(workspace_id=WS, project_id=PR, mutation_id="mut_restored")
    assert [(d.id, d.withdrawn_at is None) for d in found] == [("rd_1", False), ("rd_2", True)]
