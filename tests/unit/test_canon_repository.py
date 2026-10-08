from datetime import UTC, datetime

import pytest

from backend.domain.canon.entities import EntityLifecycle
from backend.domain.canon.propositions import Proposition, StringObject
from backend.domain.journal import CommitEnvelope, HeadConflict, RevisionConflict
from backend.domain.operations import AssertFactOp, CreateEntityOp, RetireEntityOp
from backend.domain.projection import ProjectionStatus, ProjectionWatermark
from backend.domain.validation import CanonValidationError
from tests.doubles import InMemoryCanonRepository

pytestmark = pytest.mark.unit


def _envelope(commit_id: str = "cmt_1", expected: int = 0, checksum: str = "c1") -> CommitEnvelope:
    return CommitEnvelope(
        commit_id=commit_id,
        mutation_id="mut_1",
        workspace_id="ws",
        project_id="pr",
        branch_id="br",
        revision=expected + 1,
        expected_head=expected,
        schema_version_id="scv_1",
        canonical="[]",
        checksum=checksum,
        actor_user_id="usr_1",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_apply_advances_head_and_reads_entity() -> None:
    repo = InMemoryCanonRepository()
    operation = CreateEntityOp(id="ent_1", type="Character", name="Mara")

    revision = await repo.apply_commit(envelope=_envelope(), operations=[operation])

    assert revision == 1
    assert await repo.get_head(workspace_id="ws", project_id="pr", branch_id="br") == 1
    entity = await repo.get_entity(workspace_id="ws", project_id="pr", entity_id="ent_1")
    assert entity is not None and entity.name == "Mara" and entity.is_active
    versions = await repo.list_record_versions(
        workspace_id="ws", project_id="pr", record_type="entity"
    )
    assert len(versions) == 1 and versions[0].operation_type == "create_entity"


@pytest.mark.asyncio
async def test_replay_same_commit_is_idempotent() -> None:
    repo = InMemoryCanonRepository()
    envelope = _envelope()
    operation = CreateEntityOp(id="ent_1", type="Character", name="Mara")

    assert await repo.apply_commit(envelope=envelope, operations=[operation]) == 1
    assert await repo.apply_commit(envelope=envelope, operations=[operation]) == 1
    assert len(await repo.list_record_versions(workspace_id="ws", project_id="pr")) == 1


@pytest.mark.asyncio
async def test_same_commit_id_different_checksum_conflicts() -> None:
    repo = InMemoryCanonRepository()
    operation = CreateEntityOp(id="ent_1", type="Character", name="Mara")
    await repo.apply_commit(envelope=_envelope(checksum="c1"), operations=[operation])

    with pytest.raises(RevisionConflict):
        await repo.apply_commit(envelope=_envelope(checksum="c2"), operations=[operation])


@pytest.mark.asyncio
async def test_stale_head_conflicts_and_applies_nothing() -> None:
    repo = InMemoryCanonRepository()
    repo.set_head("ws", "pr", "br", 5)

    with pytest.raises(HeadConflict):
        await repo.apply_commit(
            envelope=_envelope(expected=0),
            operations=[CreateEntityOp(id="ent_1", type="Character", name="Mara")],
        )

    assert await repo.get_entity(workspace_id="ws", project_id="pr", entity_id="ent_1") is None


@pytest.mark.asyncio
async def test_retire_fact_and_watermark() -> None:
    repo = InMemoryCanonRepository()
    await repo.apply_commit(
        envelope=_envelope(),
        operations=[CreateEntityOp(id="ent_1", type="Character", name="Mara")],
    )
    await repo.apply_commit(
        envelope=_envelope(commit_id="cmt_2", expected=1, checksum="c2"),
        operations=[RetireEntityOp(id="ent_1")],
    )

    entity = await repo.get_entity(workspace_id="ws", project_id="pr", entity_id="ent_1")
    assert entity is not None and entity.lifecycle is EntityLifecycle.RETIRED
    assert await repo.list_entities(workspace_id="ws", project_id="pr") == []
    assert (
        len(await repo.list_entities(workspace_id="ws", project_id="pr", include_retired=True)) == 1
    )

    await repo.apply_commit(
        envelope=_envelope(commit_id="cmt_3", expected=2, checksum="c3"),
        operations=[
            AssertFactOp(
                id="fact_1",
                proposition=Proposition(
                    subject_id="ent_1",
                    predicate="HAS_NAME",
                    object=StringObject(value="Mara"),
                ),
            )
        ],
    )
    fact = await repo.get_fact(workspace_id="ws", project_id="pr", fact_id="fact_1")
    assert fact is not None and fact.status.value == "asserted"

    watermark = ProjectionWatermark(
        projection="neo4j",
        workspace_id="ws",
        project_id="pr",
        branch_id="br",
        revision=3,
        commit_id="cmt_3",
        envelope_checksum="c3",
        updated_at=datetime.now(UTC),
    )
    await repo.set_watermark(watermark)
    stored = await repo.get_watermark(
        projection="neo4j", workspace_id="ws", project_id="pr", branch_id="br"
    )
    assert stored is not None and stored.revision == 3
    assert stored.status is ProjectionStatus.CURRENT


@pytest.mark.asyncio
async def test_apply_commit_runs_authoritative_validation_in_tx() -> None:
    repo = InMemoryCanonRepository()

    # A direct repository write cannot bypass validation: the authoritative
    # validator runs inside apply_commit, so an invalid operation is rejected and
    # the transaction applies nothing (head unchanged, no canon rows).
    with pytest.raises(CanonValidationError):
        await repo.apply_commit(
            envelope=_envelope(commit_id="cmt_bad", expected=0, checksum="bad"),
            operations=[CreateEntityOp(id="ent_x", type="Alien", name="Zorp")],
        )

    assert await repo.get_head(workspace_id="ws", project_id="pr", branch_id="br") == 0
    assert await repo.list_entities(workspace_id="ws", project_id="pr") == []
