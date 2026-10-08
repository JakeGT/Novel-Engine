import pytest

from backend.domain.canon.propositions import Proposition, StringObject
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import HeadConflict
from backend.domain.operations import (
    AssertFactOp,
    CreateEntityOp,
    OperationValidationError,
)
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonWriter

pytestmark = pytest.mark.application


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="u1", auth_mode=AuthMode.LOCAL, workspace_id="ws1", project_id="pr1"
    )


def _service() -> tuple[MutationService, InMemoryCanonJournal, InMemoryCanonWriter]:
    journal = InMemoryCanonJournal()
    writer = InMemoryCanonWriter()
    return MutationService(journal, writer, local_mode=True), journal, writer


@pytest.mark.asyncio
async def test_commit_creates_revision_marker_and_is_idempotent() -> None:
    service, journal, writer = _service()
    scope = _scope()
    mutation = await service.create_draft(
        scope,
        workspace_id="ws1",
        project_id="pr1",
        operations=[CreateEntityOp(id="tmp:e", type="Character", name="Mara")],
        expected_head=0,
    )

    assert (await service.validate(scope, mutation.id)).ok
    result = await service.commit(scope, mutation.id)

    assert result.status == "committed"
    assert result.revision == 1
    assert result.commit_id in writer.markers
    assert writer.markers[result.commit_id].checksum == result.checksum

    again = await service.reconcile(result.commit_id)
    assert again.status == "committed"
    assert again.revision == 1


@pytest.mark.asyncio
async def test_unknown_type_fails_validation_and_commit() -> None:
    service, _journal, _writer = _service()
    scope = _scope()
    mutation = await service.create_draft(
        scope,
        workspace_id="ws1",
        project_id="pr1",
        operations=[CreateEntityOp(id="tmp:x", type="Alien", name="Zorp")],
        expected_head=0,
    )

    report = await service.validate(scope, mutation.id)
    assert not report.ok
    assert report.issues[0].code == "unknown_type"

    with pytest.raises(OperationValidationError):
        await service.commit(scope, mutation.id)


@pytest.mark.asyncio
async def test_stale_head_is_rejected() -> None:
    service, _journal, _writer = _service()
    scope = _scope()
    first = await service.create_draft(
        scope,
        workspace_id="ws1",
        project_id="pr1",
        operations=[CreateEntityOp(id="tmp:a", type="Character", name="A")],
        expected_head=0,
    )
    await service.commit(scope, first.id)

    stale = await service.create_draft(
        scope,
        workspace_id="ws1",
        project_id="pr1",
        operations=[CreateEntityOp(id="tmp:b", type="Character", name="B")],
        expected_head=0,
    )
    with pytest.raises(HeadConflict):
        await service.commit(scope, stale.id)


@pytest.mark.asyncio
async def test_crash_mid_apply_is_reconciled_without_duplicates() -> None:
    service, journal, writer = _service()
    scope = _scope()
    mutation = await service.create_draft(
        scope,
        workspace_id="ws1",
        project_id="pr1",
        operations=[
            CreateEntityOp(id="tmp:a", type="Character", name="A"),
            CreateEntityOp(id="tmp:b", type="Location", name="B"),
        ],
        expected_head=0,
    )

    writer.fail_after_ops = 1
    with pytest.raises(RuntimeError):
        await service.commit(scope, mutation.id)

    commit_id = next(iter(journal.envelopes))
    assert commit_id not in writer.markers  # crash before the marker

    writer.fail_after_ops = None
    result = await service.reconcile(commit_id)

    assert result.status == "committed"
    assert commit_id in writer.markers
    # Both entities are present exactly once (idempotent re-apply by scoped id).
    scoped_keys = [key for key in writer.objects if key[0] == "ws1" and key[1] == "pr1"]
    assert len(scoped_keys) == 2


@pytest.mark.asyncio
async def test_fact_references_resolve_within_the_set() -> None:
    service, _journal, writer = _service()
    scope = _scope()
    mutation = await service.create_draft(
        scope,
        workspace_id="ws1",
        project_id="pr1",
        operations=[
            CreateEntityOp(id="tmp:mara", type="Character", name="Mara"),
            AssertFactOp(
                id="tmp:fact",
                proposition=Proposition(
                    subject_id="tmp:mara", predicate="HAS_NAME", object=StringObject(value="Mara")
                ),
            ),
        ],
        expected_head=0,
    )

    result = await service.commit(scope, mutation.id)
    assert result.status == "committed"
    stored = [op for op in writer.applied_operations if op.op == "assert_fact"]
    assert stored
    assert not stored[0].proposition.subject_id.startswith("tmp:")
