from datetime import UTC, datetime

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.journal import CommitEnvelope, HeadConflict
from backend.domain.operations import CreateEntityOp, RetireEntityOp

pytestmark = pytest.mark.integration

WORKSPACE = "ws_canon_it"
PROJECT = "pr_canon_it"


def _envelope(branch_id: str, commit_id: str, expected: int, checksum: str) -> CommitEnvelope:
    return CommitEnvelope(
        commit_id=commit_id,
        mutation_id="mut_canon_it",
        workspace_id=WORKSPACE,
        project_id=PROJECT,
        branch_id=branch_id,
        revision=expected + 1,
        expected_head=expected,
        schema_version_id="scv_canon_it",
        canonical="[]",
        checksum=checksum,
        actor_user_id="usr_canon_it",
        created_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def repository(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    branch = await journal.ensure_root_branch(workspace_id=WORKSPACE, project_id=PROJECT)
    try:
        yield PostgresCanonRepository(factory), journal, branch
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_commit_is_atomic_idempotent_and_advances_head(repository) -> None:
    repo, journal, branch = repository
    operation = CreateEntityOp(id="ent_canon_it", type="Character", name="Ivo")
    envelope = _envelope(branch.id, "cmt_canon_it_1", 0, "ck1")

    revision = await repo.apply_commit(envelope=envelope, operations=[operation])
    assert revision == 1

    after = await journal.get_branch(
        workspace_id=WORKSPACE, project_id=PROJECT, branch_id=branch.id
    )
    assert after is not None and after.head_revision == 1

    entity = await repo.get_entity(
        workspace_id=WORKSPACE, project_id=PROJECT, entity_id="ent_canon_it"
    )
    assert entity is not None and entity.name == "Ivo"

    # Replaying the same commit id + checksum is a no-op, not a duplicate.
    assert await repo.apply_commit(envelope=envelope, operations=[operation]) == 1
    versions = await repo.list_record_versions(
        workspace_id=WORKSPACE, project_id=PROJECT, record_type="entity"
    )
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_stale_head_commit_rolls_back(repository) -> None:
    repo, journal, branch = repository
    with pytest.raises(HeadConflict):
        await repo.apply_commit(
            envelope=_envelope(branch.id, "cmt_canon_it_bad", 9, "ckbad"),
            operations=[CreateEntityOp(id="ent_bad", type="Character", name="Nope")],
        )

    assert (
        await repo.get_entity(workspace_id=WORKSPACE, project_id=PROJECT, entity_id="ent_bad")
        is None
    )
    branch_after = await journal.get_branch(
        workspace_id=WORKSPACE, project_id=PROJECT, branch_id=branch.id
    )
    assert branch_after is not None and branch_after.head_revision == 0


@pytest.mark.asyncio
async def test_retire_entity_updates_normalized_state(repository) -> None:
    repo, _journal, branch = repository
    await repo.apply_commit(
        envelope=_envelope(branch.id, "cmt_canon_it_a", 0, "cka"),
        operations=[CreateEntityOp(id="ent_r", type="Character", name="Retire Me")],
    )
    await repo.apply_commit(
        envelope=_envelope(branch.id, "cmt_canon_it_b", 1, "ckb"),
        operations=[RetireEntityOp(id="ent_r")],
    )

    entity = await repo.get_entity(workspace_id=WORKSPACE, project_id=PROJECT, entity_id="ent_r")
    assert entity is not None and not entity.is_active
    assert entity.retired_revision == 2
    assert await repo.list_entities(workspace_id=WORKSPACE, project_id=PROJECT) == []


@pytest.mark.asyncio
async def test_invalid_commit_rolls_back_without_advancing_head(repository) -> None:
    from backend.domain.validation import CanonValidationError

    repo, journal, branch = repository
    bad = CreateEntityOp(id="ent_bad", type="Alien", name="Zorp")
    envelope = _envelope(branch.id, "cmt_bad", 0, "ckbad")

    with pytest.raises(CanonValidationError):
        await repo.apply_commit(envelope=envelope, operations=[bad])

    # The authoritative validator rejected the commit inside the transaction, so
    # nothing was written: head stays 0 and no canon row exists.
    after = await journal.get_branch(
        workspace_id=WORKSPACE, project_id=PROJECT, branch_id=branch.id
    )
    assert after is not None and after.head_revision == 0
    entity = await repo.get_entity(workspace_id=WORKSPACE, project_id=PROJECT, entity_id="ent_bad")
    assert entity is None
