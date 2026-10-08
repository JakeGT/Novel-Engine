from datetime import UTC, datetime

import pytest
import pytest_asyncio

from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.journal import CommitEnvelope, HeadConflict, RevisionConflict

pytestmark = pytest.mark.integration

WS = "ws-journal"
PROJECT = "prj-journal"


def _envelope(commit_id: str, revision: int, expected_head: int, checksum: str, branch_id: str):
    return CommitEnvelope(
        commit_id=commit_id,
        mutation_id="mut-1",
        workspace_id=WS,
        project_id=PROJECT,
        branch_id=branch_id,
        revision=revision,
        expected_head=expected_head,
        schema_version_id=None,
        canonical="{}",
        checksum=checksum,
        actor_user_id="user-1",
        created_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def journal(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresCanonJournal(create_session_factory(engine))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_root_branch_head_reservation_and_finalize(journal) -> None:
    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    again = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    assert branch.id == again.id
    assert branch.head_revision == 0

    mutation = await journal.create_mutation(
        workspace_id=WS, project_id=PROJECT, branch_id=branch.id, expected_head=0
    )
    reservation = await journal.reserve_head(
        workspace_id=WS,
        project_id=PROJECT,
        branch_id=branch.id,
        mutation_id=mutation.id,
        expected_revision=0,
    )
    assert reservation.reserved_revision == 1

    # One active reservation only.
    with pytest.raises(HeadConflict):
        await journal.reserve_head(
            workspace_id=WS,
            project_id=PROJECT,
            branch_id=branch.id,
            mutation_id=mutation.id,
            expected_revision=0,
        )

    await journal.append_envelope(
        _envelope("cmt-1", 1, expected_head=0, checksum="aaa", branch_id=branch.id)
    )
    revision = await journal.finalize(commit_id="cmt-1")
    assert revision.revision == 1
    assert revision.result_head == 1

    # Head cannot advance twice from the same expected revision.
    with pytest.raises(HeadConflict):
        await journal.reserve_head(
            workspace_id=WS,
            project_id=PROJECT,
            branch_id=branch.id,
            mutation_id=mutation.id,
            expected_revision=0,
        )

    advanced = await journal.reserve_head(
        workspace_id=WS,
        project_id=PROJECT,
        branch_id=branch.id,
        mutation_id=mutation.id,
        expected_revision=1,
    )
    assert advanced.reserved_revision == 2

    outbox = await journal.list_outbox(workspace_id=WS, project_id=PROJECT)
    assert len(outbox) == 1
    assert outbox[0].idempotency_key == "commit:cmt-1"


@pytest.mark.asyncio
async def test_commit_id_is_immutable(journal) -> None:
    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    await journal.append_envelope(
        _envelope("cmt-2", 1, expected_head=0, checksum="aaa", branch_id=branch.id)
    )
    await journal.append_envelope(
        _envelope("cmt-2", 1, expected_head=0, checksum="aaa", branch_id=branch.id)
    )
    with pytest.raises(RevisionConflict):
        await journal.append_envelope(
            _envelope("cmt-2", 1, expected_head=0, checksum="bbb", branch_id=branch.id)
        )


@pytest.mark.asyncio
async def test_project_isolation(journal) -> None:
    await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    await journal.ensure_root_branch(workspace_id=WS, project_id="prj-other")
    assert len(await journal.list_branches(workspace_id=WS, project_id=PROJECT)) == 1
    assert (
        await journal.get_branch(workspace_id=WS, project_id="prj-other", branch_id="missing")
        is None
    )
