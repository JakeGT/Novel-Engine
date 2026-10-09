from datetime import UTC, datetime

import pytest

from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.journal import CommitEnvelope, MutationOperation
from backend.domain.operations import (
    AssertFactOp,
    CreateEntityOp,
    canonicalize_operations,
    operations_checksum,
)
from backend.services.canon_archive import ArchiveError, CanonArchiveService, _checksum
from tests.doubles import (
    InMemoryCanonJournal,
    InMemoryCanonRepository,
    InMemoryMediaStore,
    InMemoryObjectStore,
    InMemorySchemaVersionStore,
)

pytestmark = pytest.mark.application


async def _service() -> tuple[CanonArchiveService, InMemoryCanonJournal, InMemoryCanonRepository]:
    journal = InMemoryCanonJournal()
    canon = InMemoryCanonRepository(journal=journal)
    service = CanonArchiveService(
        canon=canon,
        journal=journal,
        schemas=InMemorySchemaVersionStore(),
        media=InMemoryMediaStore(),
        objects=InMemoryObjectStore(),
    )
    return service, journal, canon


async def _seed(service: CanonArchiveService, journal, canon, objects, media) -> str:
    ws, pr = "ws_a", "pr_a"
    branch = await journal.ensure_root_branch(workspace_id=ws, project_id=pr, name="main")
    operations = [CreateEntityOp(id="ent_1", type="Character", name="Mara")]
    mutation = await journal.create_mutation(
        workspace_id=ws,
        project_id=pr,
        branch_id=branch.id,
        expected_head=0,
        actor_user_id="usr_source",
        origin="model",
        correlation_id="archive-test",
    )
    await journal.record_operations(
        mutation_id=mutation.id,
        operations=[
            MutationOperation(
                ordinal=0,
                operation_type=operations[0].op,
                payload=operations[0].model_dump(mode="json"),
            )
        ],
    )
    await journal.record_decision(
        mutation_id=mutation.id,
        decision="approved",
        actor_user_id="usr_reviewer",
        note="reviewed for archive",
    )
    envelope = CommitEnvelope(
        commit_id="cmt_1",
        mutation_id=mutation.id,
        workspace_id=ws,
        project_id=pr,
        branch_id=branch.id,
        revision=1,
        expected_head=0,
        canonical=canonicalize_operations(operations),
        checksum=operations_checksum(operations),
        actor_user_id="usr_source",
        created_at=datetime.now(UTC),
    )
    await canon.apply_commit(envelope=envelope, operations=operations)

    stat = objects.put(b"hello world", workspace_id=ws, project_id=pr, content_type="text/plain")
    asset = await media.create_asset(workspace_id=ws, project_id=pr, name="Reference")
    await media.create_version(
        workspace_id=ws,
        project_id=pr,
        asset_id=asset.id,
        object_key=stat.key,
        content_type="text/plain",
        byte_length=11,
        checksum=stat.checksum,
    )
    return ws, pr


@pytest.mark.asyncio
async def test_archive_round_trip_preserves_canonical_checksum() -> None:
    service, journal, canon = await _service()
    objects = service._objects
    media = service._media
    ws, pr = await _seed(service, journal, canon, objects, media)

    archive = await service.export_project(ws, pr)
    assert archive["format_version"] == "3"
    assert len(archive["commits"]) == 1
    assert len(archive["media"]["versions"]) == 1

    result = await service.import_project("ws_b", "pr_b", archive)
    assert result["commits"] == 1
    assert result["assets"] == 1

    branch_b = await journal.ensure_root_branch(workspace_id="ws_b", project_id="pr_b", name="main")
    assert await canon.get_head(workspace_id="ws_b", project_id="pr_b", branch_id=branch_b.id) == 1
    assert (
        await canon.get_entity(workspace_id="ws_b", project_id="pr_b", entity_id="ent_1")
    ) is not None
    imported_revision = (
        await canon.list_commits(
            workspace_id="ws_b", project_id="pr_b", branch_id=branch_b.id, limit=10
        )
    )[0]
    imported_envelope = await canon.get_envelope(imported_revision.commit_id)
    assert imported_envelope is not None
    imported_mutation = await journal.get_mutation(
        workspace_id="ws_b",
        project_id="pr_b",
        mutation_id=imported_envelope.mutation_id,
    )
    assert imported_mutation is not None and imported_mutation.origin == "model"
    imported_decisions = await journal.list_decisions(mutation_id=imported_mutation.id)
    assert [(item.decision, item.actor_user_id) for item in imported_decisions] == [
        ("approved", "usr_reviewer")
    ]
    assert await service.accepted_checksum(ws, pr) == await service.accepted_checksum(
        "ws_b", "pr_b"
    )

    # Idempotent: re-importing the same archive does not duplicate.
    again = await service.import_project("ws_b", "pr_b", archive)
    assert again["commits"] == 1
    assert await canon.get_head(workspace_id="ws_b", project_id="pr_b", branch_id=branch_b.id) == 1

    # Target-scoped commit and mutation ids allow the same archive to seed more
    # than one project without colliding in the global journal tables.
    await service.import_project("ws_c", "pr_c", archive)
    branch_c = await journal.ensure_root_branch(workspace_id="ws_c", project_id="pr_c", name="main")
    assert await canon.get_head(workspace_id="ws_c", project_id="pr_c", branch_id=branch_c.id) == 1


@pytest.mark.asyncio
async def test_archive_rejects_checksum_mismatch() -> None:
    service, journal, canon = await _service()
    ws, pr = await _seed(service, journal, canon, service._objects, service._media)
    archive = await service.export_project(ws, pr)

    archive["canon"]["entities"][0]["name"] = "Tampered"
    with pytest.raises(ArchiveError, match="checksum"):
        await service.import_project("ws_b", "pr_b", archive)


@pytest.mark.asyncio
async def test_archive_rejects_unsafe_object_key() -> None:
    service, journal, canon = await _service()
    ws, pr = await _seed(service, journal, canon, service._objects, service._media)
    archive = await service.export_project(ws, pr)

    archive["objects"][0]["key"] = "../escape"
    archive["checksum"] = _checksum(archive)
    with pytest.raises(ArchiveError, match="unsafe object key"):
        await service.import_project("ws_b", "pr_b", archive)


@pytest.mark.asyncio
async def test_archive_remaps_entity_reference_project_scope() -> None:
    service, journal, canon = await _service()
    branch = await journal.ensure_root_branch(workspace_id="ws_a", project_id="pr_a")
    operations = [
        CreateEntityOp(id="ent_char", type="Character", name="Mara"),
        CreateEntityOp(id="ent_place", type="Location", name="Harbor"),
        AssertFactOp(
            id="fact_place",
            proposition=Proposition(
                subject_id="ent_char",
                predicate="LOCATED_AT",
                object=EntityRefObject(entity_id="ent_place", project_id="pr_a"),
            ),
        ),
    ]
    await canon.apply_commit(
        envelope=CommitEnvelope(
            commit_id="cmt_scope",
            mutation_id="mut_scope",
            workspace_id="ws_a",
            project_id="pr_a",
            branch_id=branch.id,
            revision=1,
            expected_head=0,
            canonical=canonicalize_operations(operations),
            checksum=operations_checksum(operations),
            created_at=datetime.now(UTC),
        ),
        operations=operations,
    )

    archive = await service.export_project("ws_a", "pr_a")
    await service.import_project("ws_b", "pr_b", archive)
    fact = await canon.get_fact(workspace_id="ws_b", project_id="pr_b", fact_id="fact_place")
    assert fact is not None
    assert isinstance(fact.proposition.object, EntityRefObject)
    assert fact.proposition.object.project_id == "pr_b"


@pytest.mark.asyncio
async def test_archive_rejects_nonempty_unrelated_target_before_writes() -> None:
    service, journal, canon = await _service()
    ws, pr = await _seed(service, journal, canon, service._objects, service._media)
    archive = await service.export_project(ws, pr)

    target_branch = await journal.ensure_root_branch(workspace_id="ws_b", project_id="pr_b")
    target_operations = [CreateEntityOp(id="ent_existing", type="Character", name="Existing")]
    await canon.apply_commit(
        envelope=CommitEnvelope(
            commit_id="cmt_existing",
            mutation_id="mut_existing",
            workspace_id="ws_b",
            project_id="pr_b",
            branch_id=target_branch.id,
            revision=1,
            expected_head=0,
            canonical=canonicalize_operations(target_operations),
            checksum=operations_checksum(target_operations),
            created_at=datetime.now(UTC),
        ),
        operations=target_operations,
    )

    with pytest.raises(ArchiveError, match="unrelated canon"):
        await service.import_project("ws_b", "pr_b", archive)
    assert service._objects.list_objects(workspace_id="ws_b", project_id="pr_b") == []
