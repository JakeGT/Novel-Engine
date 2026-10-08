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
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository

pytestmark = pytest.mark.application


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="u1", auth_mode=AuthMode.LOCAL, workspace_id="ws1", project_id="pr1"
    )


def _service() -> tuple[MutationService, InMemoryCanonJournal, InMemoryCanonRepository]:
    journal = InMemoryCanonJournal()
    repository = InMemoryCanonRepository()
    return MutationService(journal, repository, local_mode=True), journal, repository


@pytest.mark.asyncio
async def test_commit_creates_revision_and_is_idempotent() -> None:
    service, _journal, repository = _service()
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
    # Canonical state landed in PostgreSQL canon, not the graph projection.
    assert repository.envelopes[result.commit_id].checksum == result.checksum
    assert (
        await repository.get_head(
            workspace_id="ws1", project_id="pr1", branch_id=mutation.branch_id
        )
        == 1
    )

    again = await service.reconcile(result.commit_id)
    assert again.status == "committed"
    assert again.revision == 1


@pytest.mark.asyncio
async def test_unknown_type_fails_validation_and_commit() -> None:
    service, _journal, _repository = _service()
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
    service, _journal, _repository = _service()
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
async def test_commit_lands_entity_in_canonical_state() -> None:
    service, _journal, repository = _service()
    scope = _scope()
    mutation = await service.create_draft(
        scope,
        workspace_id="ws1",
        project_id="pr1",
        operations=[CreateEntityOp(id="tmp:a", type="Character", name="A")],
        expected_head=0,
    )

    await service.commit(scope, mutation.id)

    listed = await repository.list_entities(workspace_id="ws1", project_id="pr1")
    assert [item.name for item in listed] == ["A"]


@pytest.mark.asyncio
async def test_fact_references_resolve_within_the_set() -> None:
    service, _journal, repository = _service()
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

    facts = await repository.list_facts(workspace_id="ws1", project_id="pr1")
    assert len(facts) == 1
    assert not facts[0].proposition.subject_id.startswith("tmp:")
