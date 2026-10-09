from datetime import UTC, datetime

import pytest

from backend.domain.canon.revisions import CanonRevision
from backend.domain.journal import (
    CommitEnvelope,
    HeadConflict,
    MutationOperation,
    RevisionConflict,
)
from tests.doubles import InMemoryCanonJournal

pytestmark = pytest.mark.unit

WS = "ws-1"
PROJECT = "prj-1"


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


@pytest.mark.asyncio
async def test_root_branch_is_idempotent() -> None:
    journal = InMemoryCanonJournal()
    first = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    second = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    assert first.id == second.id
    assert first.head_revision == 0
    assert len(await journal.list_branches(workspace_id=WS, project_id=PROJECT)) == 1


@pytest.mark.asyncio
async def test_head_cannot_advance_twice_from_same_expected_revision() -> None:
    journal = InMemoryCanonJournal()
    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
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

    # A second reservation while one is active is refused.
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

    # Head is now 1; a stale expected revision must still be refused.
    with pytest.raises(HeadConflict):
        await journal.reserve_head(
            workspace_id=WS,
            project_id=PROJECT,
            branch_id=branch.id,
            mutation_id=mutation.id,
            expected_revision=0,
        )

    ok = await journal.reserve_head(
        workspace_id=WS,
        project_id=PROJECT,
        branch_id=branch.id,
        mutation_id=mutation.id,
        expected_revision=1,
    )
    assert ok.reserved_revision == 2


@pytest.mark.asyncio
async def test_commit_id_immutability() -> None:
    journal = InMemoryCanonJournal()
    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    await journal.append_envelope(
        _envelope("cmt-1", 1, expected_head=0, checksum="aaa", branch_id=branch.id)
    )
    # Same id + same checksum is an idempotent no-op.
    await journal.append_envelope(
        _envelope("cmt-1", 1, expected_head=0, checksum="aaa", branch_id=branch.id)
    )
    # Same id + different checksum is a conflict.
    with pytest.raises(RevisionConflict):
        await journal.append_envelope(
            _envelope("cmt-1", 1, expected_head=0, checksum="bbb", branch_id=branch.id)
        )


@pytest.mark.asyncio
async def test_mutation_idempotency_key_and_operations() -> None:
    journal = InMemoryCanonJournal()
    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    first = await journal.create_mutation(
        workspace_id=WS,
        project_id=PROJECT,
        branch_id=branch.id,
        expected_head=0,
        idempotency_key="key-1",
    )
    second = await journal.create_mutation(
        workspace_id=WS,
        project_id=PROJECT,
        branch_id=branch.id,
        expected_head=0,
        idempotency_key="key-1",
    )
    assert first.id == second.id

    await journal.record_operations(
        mutation_id=first.id,
        operations=[
            MutationOperation(ordinal=1, operation_type="create_entity", payload={"id": "e1"}),
            MutationOperation(ordinal=0, operation_type="create_entity", payload={"id": "e0"}),
        ],
    )
    with pytest.raises(RevisionConflict):
        await journal.record_operations(
            mutation_id=first.id,
            operations=[
                MutationOperation(ordinal=1, operation_type="create_entity", payload={"x": 1})
            ],
        )
    operations = await journal.list_operations(mutation_id=first.id)
    assert [op.ordinal for op in operations] == [0, 1]
    assert operations[1].payload == {"id": "e1"}


@pytest.mark.asyncio
async def test_finalize_is_idempotent_and_writes_one_outbox_message() -> None:
    journal = InMemoryCanonJournal()
    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PROJECT)
    await journal.append_envelope(
        _envelope("cmt-1", 1, expected_head=0, checksum="aaa", branch_id=branch.id)
    )
    first = await journal.finalize(commit_id="cmt-1")
    second = await journal.finalize(commit_id="cmt-1")
    assert isinstance(first, CanonRevision)
    assert first.revision == second.revision
    outbox = await journal.list_outbox(workspace_id=WS, project_id=PROJECT)
    assert len(outbox) == 1
    assert outbox[0].idempotency_key == "commit:cmt-1"
