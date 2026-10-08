from datetime import UTC, datetime

import pytest

from backend.domain.journal import CommitEnvelope
from backend.domain.operations import (
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
    envelope = CommitEnvelope(
        commit_id="cmt_1",
        mutation_id="m1",
        workspace_id=ws,
        project_id=pr,
        branch_id=branch.id,
        revision=1,
        expected_head=0,
        canonical=canonicalize_operations(operations),
        checksum=operations_checksum(operations),
        actor_user_id=None,
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
    assert await service.accepted_checksum(ws, pr) == await service.accepted_checksum(
        "ws_b", "pr_b"
    )

    # Idempotent: re-importing the same archive does not duplicate.
    again = await service.import_project("ws_b", "pr_b", archive)
    assert again["commits"] == 1
    assert await canon.get_head(workspace_id="ws_b", project_id="pr_b", branch_id=branch_b.id) == 1


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
