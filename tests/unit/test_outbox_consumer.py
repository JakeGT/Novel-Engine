import pytest

from backend.domain.identity import utcnow
from backend.domain.journal import CommitEnvelope
from backend.domain.operations import CreateEntityOp, canonicalize_operations, operations_checksum
from backend.services.outbox_consumer import OutboxConsumer
from backend.services.projection_service import GraphProjectionService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository, InMemoryCanonWriter

pytestmark = pytest.mark.application

WS = "ws1"
PR = "prj1"


async def _commit(
    repo: InMemoryCanonRepository, branch_id: str, revision: int, expected: int
) -> None:
    ops = [CreateEntityOp(id="ent_1", type="Character", name="Mara")]
    envelope = CommitEnvelope(
        commit_id=f"cmt_{revision}",
        mutation_id="mut_1",
        workspace_id=WS,
        project_id=PR,
        branch_id=branch_id,
        revision=revision,
        expected_head=expected,
        canonical=canonicalize_operations(ops),
        checksum=operations_checksum(ops),
        actor_user_id="u1",
        created_at=utcnow(),
    )
    await repo.apply_commit(envelope=envelope, operations=ops)


@pytest.mark.asyncio
async def test_drain_projects_and_marks_published() -> None:
    journal = InMemoryCanonJournal()
    writer = InMemoryCanonWriter()
    repo = InMemoryCanonRepository(journal=journal)
    projector = GraphProjectionService(canon=repo, writer=writer)
    consumer = OutboxConsumer(journal=journal, projector=projector)

    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PR)
    await _commit(repo, branch.id, 1, 0)

    result = await consumer.drain(workspace_id=WS, project_id=PR, branch_id=branch.id)
    assert result.published == 1
    assert result.failed == 0
    assert result.lag == 0
    assert await writer.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_1") is not None

    # Idempotent: nothing left to publish.
    again = await consumer.drain(workspace_id=WS, project_id=PR, branch_id=branch.id)
    assert again.processed == 0


@pytest.mark.asyncio
async def test_projection_failure_is_visible_lag_and_retryable() -> None:
    journal = InMemoryCanonJournal()
    writer = InMemoryCanonWriter()
    writer.fail_after_ops = 0
    repo = InMemoryCanonRepository(journal=journal)
    projector = GraphProjectionService(canon=repo, writer=writer)
    consumer = OutboxConsumer(journal=journal, projector=projector)

    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PR)
    await _commit(repo, branch.id, 1, 0)

    result = await consumer.drain(workspace_id=WS, project_id=PR, branch_id=branch.id)
    assert result.failed == 1
    assert result.published == 0
    assert result.lag == 1
    message_id = next(iter(journal.outbox.values())).id
    assert journal.outbox_attempts[message_id] == 1

    # Canon is already committed; clearing the fault lets the projection retry.
    writer.fail_after_ops = None
    retry = await consumer.drain(workspace_id=WS, project_id=PR, branch_id=branch.id)
    assert retry.failed == 0
    assert retry.published == 1
    assert retry.lag == 0
    assert await writer.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_1") is not None
