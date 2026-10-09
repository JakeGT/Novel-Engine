from datetime import UTC, datetime

import pytest

from backend.domain.canon.entities import EntityLifecycle
from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.journal import CommitEnvelope, HeadConflict, RevisionConflict
from backend.domain.operations import (
    AmendEventOp,
    AssertFactOp,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    RetireAssetLinkOp,
    RetireEntityOp,
    RetireEventOp,
    canonicalize_operations,
    operations_checksum,
)
from backend.domain.projection import ProjectionStatus, ProjectionWatermark
from backend.domain.validation import CanonValidationError
from tests.doubles import InMemoryCanonRepository

pytestmark = pytest.mark.unit


def _envelope(
    operations,
    commit_id: str = "cmt_1",
    expected: int = 0,
    checksum: str | None = None,
) -> CommitEnvelope:
    return CommitEnvelope(
        commit_id=commit_id,
        mutation_id=f"mut_{commit_id}",
        workspace_id="ws",
        project_id="pr",
        branch_id="br",
        revision=expected + 1,
        expected_head=expected,
        schema_version_id="scv_1",
        canonical=canonicalize_operations(operations),
        checksum=checksum or operations_checksum(operations),
        actor_user_id="usr_1",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_apply_advances_head_and_reads_entity() -> None:
    repo = InMemoryCanonRepository()
    operation = CreateEntityOp(id="ent_1", type="Character", name="Mara")

    revision = await repo.apply_commit(envelope=_envelope([operation]), operations=[operation])

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
    operation = CreateEntityOp(id="ent_1", type="Character", name="Mara")
    envelope = _envelope([operation])

    assert await repo.apply_commit(envelope=envelope, operations=[operation]) == 1
    assert await repo.apply_commit(envelope=envelope, operations=[operation]) == 1
    assert len(await repo.list_record_versions(workspace_id="ws", project_id="pr")) == 1


@pytest.mark.asyncio
async def test_same_commit_id_different_checksum_conflicts() -> None:
    repo = InMemoryCanonRepository()
    operation = CreateEntityOp(id="ent_1", type="Character", name="Mara")
    await repo.apply_commit(envelope=_envelope([operation]), operations=[operation])

    with pytest.raises(RevisionConflict):
        await repo.apply_commit(
            envelope=_envelope([operation], checksum="different"), operations=[operation]
        )


@pytest.mark.asyncio
async def test_stale_head_conflicts_and_applies_nothing() -> None:
    repo = InMemoryCanonRepository()
    repo.set_head("ws", "pr", "br", 5)

    with pytest.raises(HeadConflict):
        await repo.apply_commit(
            envelope=_envelope(
                [CreateEntityOp(id="ent_1", type="Character", name="Mara")], expected=0
            ),
            operations=[CreateEntityOp(id="ent_1", type="Character", name="Mara")],
        )

    assert await repo.get_entity(workspace_id="ws", project_id="pr", entity_id="ent_1") is None


@pytest.mark.asyncio
async def test_retire_fact_and_watermark() -> None:
    repo = InMemoryCanonRepository()
    create = CreateEntityOp(id="ent_1", type="Character", name="Mara")
    await repo.apply_commit(envelope=_envelope([create]), operations=[create])
    retire = RetireEntityOp(id="ent_1")
    # A direct author write may not skip the approval a destructive change needs.
    with pytest.raises(CanonValidationError, match="review_required"):
        await repo.apply_commit(
            envelope=_envelope([retire], commit_id="cmt_2", expected=1), operations=[retire]
        )
    await repo.apply_commit(
        envelope=_envelope([retire], commit_id="cmt_2", expected=1),
        operations=[retire],
        origin="migration",
    )

    entity = await repo.get_entity(workspace_id="ws", project_id="pr", entity_id="ent_1")
    assert entity is not None and entity.lifecycle is EntityLifecycle.RETIRED
    assert await repo.list_entities(workspace_id="ws", project_id="pr") == []
    assert (
        len(await repo.list_entities(workspace_id="ws", project_id="pr", include_retired=True)) == 1
    )

    with pytest.raises(CanonValidationError, match="retired_reference"):
        fact_op = AssertFactOp(
            id="fact_1",
            proposition=Proposition(
                subject_id="ent_1",
                predicate="HAS_NAME",
                object=StringObject(value="Mara"),
            ),
        )
        await repo.apply_commit(
            envelope=_envelope([fact_op], commit_id="cmt_3", expected=2),
            operations=[fact_op],
        )
    assert await repo.get_fact(workspace_id="ws", project_id="pr", fact_id="fact_1") is None
    assert await repo.get_head(workspace_id="ws", project_id="pr", branch_id="br") == 2

    watermark = ProjectionWatermark(
        projection="neo4j",
        workspace_id="ws",
        project_id="pr",
        branch_id="br",
        revision=2,
        commit_id="cmt_2",
        envelope_checksum=operations_checksum([retire]),
        updated_at=datetime.now(UTC),
    )
    await repo.set_watermark(watermark)
    stored = await repo.get_watermark(
        projection="neo4j", workspace_id="ws", project_id="pr", branch_id="br"
    )
    assert stored is not None and stored.revision == 2
    assert stored.status is ProjectionStatus.CURRENT


@pytest.mark.asyncio
async def test_apply_commit_runs_authoritative_validation_in_tx() -> None:
    repo = InMemoryCanonRepository()

    # A direct repository write cannot bypass validation: the authoritative
    # validator runs inside apply_commit, so an invalid operation is rejected and
    # the transaction applies nothing (head unchanged, no canon rows).
    with pytest.raises(CanonValidationError):
        invalid = CreateEntityOp(id="ent_x", type="Alien", name="Zorp")
        await repo.apply_commit(
            envelope=_envelope([invalid], commit_id="cmt_bad", expected=0),
            operations=[invalid],
        )

    assert await repo.get_head(workspace_id="ws", project_id="pr", branch_id="br") == 0
    assert await repo.list_entities(workspace_id="ws", project_id="pr") == []


@pytest.mark.asyncio
async def test_envelope_must_match_supplied_operations() -> None:
    repo = InMemoryCanonRepository()
    reviewed = CreateEntityOp(id="ent_a", type="Character", name="A")
    substituted = CreateEntityOp(id="ent_b", type="Character", name="B")

    with pytest.raises(RevisionConflict, match="supplied operations"):
        await repo.apply_commit(
            envelope=_envelope([reviewed], commit_id="cmt_substitution"),
            operations=[substituted],
        )
    assert await repo.get_head(workspace_id="ws", project_id="pr", branch_id="br") == 0


@pytest.mark.asyncio
async def test_update_style_operations_require_existing_active_records() -> None:
    repo = InMemoryCanonRepository()
    operations = [
        CloseFactOp(id="fact_missing"),
        CloseBeliefOp(id="belief_missing"),
        AmendEventOp(id="event_missing", name="No event"),
        RetireEventOp(id="event_missing"),
        RetireAssetLinkOp(id="link_missing"),
    ]

    with pytest.raises(CanonValidationError) as raised:
        await repo.apply_commit(
            envelope=_envelope(operations, commit_id="cmt_missing_records"),
            operations=operations,
            origin="migration",
        )
    assert {finding.code for finding in raised.value.report.blocking} == {
        "unknown_fact",
        "unknown_belief",
        "unknown_event",
        "unknown_asset_link",
    }
    assert await repo.get_head(workspace_id="ws", project_id="pr", branch_id="br") == 0


@pytest.mark.asyncio
async def test_cardinality_is_enforced_within_one_operation_batch() -> None:
    repo = InMemoryCanonRepository()
    operations = [
        CreateEntityOp(id="ent_char", type="Character", name="Mara"),
        CreateEntityOp(id="ent_north", type="Location", name="North Gate"),
        CreateEntityOp(id="ent_south", type="Location", name="South Gate"),
        AssertFactOp(
            id="fact_north",
            proposition=Proposition(
                subject_id="ent_char",
                predicate="LOCATED_AT",
                object=EntityRefObject(entity_id="ent_north", project_id="pr"),
            ),
        ),
        AssertFactOp(
            id="fact_south",
            proposition=Proposition(
                subject_id="ent_char",
                predicate="LOCATED_AT",
                object=EntityRefObject(entity_id="ent_south", project_id="pr"),
            ),
        ),
    ]

    with pytest.raises(CanonValidationError, match="cardinality_conflict"):
        await repo.apply_commit(
            envelope=_envelope(operations, commit_id="cmt_batch_cardinality"),
            operations=operations,
        )


@pytest.mark.asyncio
async def test_entity_reference_cannot_cross_project_scope() -> None:
    repo = InMemoryCanonRepository()
    operations = [
        CreateEntityOp(id="ent_char", type="Character", name="Mara"),
        CreateEntityOp(id="ent_place", type="Location", name="Harbor"),
        AssertFactOp(
            id="fact_place",
            proposition=Proposition(
                subject_id="ent_char",
                predicate="LOCATED_AT",
                object=EntityRefObject(entity_id="ent_place", project_id="another-project"),
            ),
        ),
    ]

    with pytest.raises(CanonValidationError, match="cross_project_reference"):
        await repo.apply_commit(
            envelope=_envelope(operations, commit_id="cmt_cross_project"),
            operations=operations,
        )
