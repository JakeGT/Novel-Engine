"""Picture processing state, copies and segments against PostgreSQL (AST-003, 0046)."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.job_store import PostgresJobStore
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaProcessingStore, PostgresMediaStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.media import ProcessingStatus, VariantKind
from backend.domain.operations import CreateEntityOp
from backend.services.job_runner import JobRunner
from backend.services.media_service import MEDIA_PROCESS_KIND, MediaService
from backend.services.mutation_service import MutationService
from tests.doubles import FakeImageProcessor, InMemoryObjectStore

pytestmark = pytest.mark.integration

WS, PR = "ws_pics_it", "pr_pics_it"
SCOPE = AccessScope(
    actor_user_id="usr_pics", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
)


def _png(color: str = "teal") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(out, format="PNG")
    return out.getvalue()


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    repository = PostgresCanonRepository(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    jobs = PostgresJobStore(factory)
    runner = JobRunner(store=jobs, journal=journal, local_mode=True, autostart=False)
    processing = PostgresMediaProcessingStore(factory)
    processor = FakeImageProcessor()
    objects = InMemoryObjectStore()
    media = MediaService(
        media=PostgresMediaStore(factory),
        objects=objects,
        mutation=mutation,
        reader=repository,
        journal=journal,
        local_mode=True,
        processing=processing,
        processor=processor,
        runner=runner,
    )
    media.register_jobs()
    draft = await mutation.create_draft(
        SCOPE,
        workspace_id=WS,
        project_id=PR,
        operations=[CreateEntityOp(id="ent_pic", type="Character", name="Mara")],
        expected_head=0,
    )
    await mutation.commit(SCOPE, draft.id)
    try:
        yield media, processing, runner, objects, factory, processor
    finally:
        await engine.dispose()


async def _upload(media: MediaService, revision: int, color: str = "teal"):
    result, link, version = await media.add_picture(
        SCOPE, WS, PR, entity_id="ent_pic", data=_png(color), name=None, expected_revision=revision
    )
    return result.revision, link, version


@pytest.mark.asyncio
async def test_the_tables_exist_with_their_rules(stack) -> None:
    *_rest, factory, _processor = stack
    async with factory() as session:
        tables = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_name IN "
                        "('media_processing', 'media_variants', 'media_segments')"
                    )
                )
            ).all()
        }
        assert tables == {"media_processing", "media_variants", "media_segments"}
        with pytest.raises(Exception):  # noqa: B017,PT011 - the CHECK refuses an unknown state
            await session.execute(
                text(
                    "INSERT INTO media_processing (workspace_id, project_id, asset_version_id, "
                    "asset_id, status, processor_version, source_checksum) VALUES "
                    "('w', 'p', 'v', 'a', 'exploded', 'x', 'y')"
                )
            )


@pytest.mark.asyncio
async def test_a_picture_is_prepared_through_the_queue_once(stack) -> None:
    media, processing, runner, objects, _factory, processor = stack
    _revision, link, version = await _upload(media, 1)

    lag = await processing.lag()
    assert lag["waiting"] >= 1 and lag["oldest_seconds"] is not None
    [job] = await runner.run_pending()
    assert job.kind == MEDIA_PROCESS_KIND and job.result["outcome"] == "ready"

    state = await processing.get_processing(workspace_id=WS, project_id=PR, version_id=version.id)
    assert state is not None and state.status is ProcessingStatus.READY and state.attempts == 1
    assert (state.width, state.height, state.detected_format) == (640, 480, "PNG")
    thumbnail = await processing.get_variant(
        workspace_id=WS, project_id=PR, version_id=version.id, kind=VariantKind.THUMBNAIL
    )
    assert thumbnail is not None and thumbnail.source_checksum == version.checksum
    [whole] = await processing.list_segments(workspace_id=WS, project_id=PR, version_id=version.id)
    assert whole.kind.value == "whole" and whole.width == 640

    # Idempotent: preparing again changes nothing; the backfill has nothing to queue.
    assert await media.process_version(WS, PR, version.id) == {"outcome": "already_done"}
    assert processor.calls == 1
    assert (await media.queue_backfill(workspace_id=WS)).queued == 0
    used = await processing.workspace_bytes(workspace_id=WS)
    assert used == version.byte_length + sum(
        copy.byte_length
        for copy in (
            await processing.list_variants(workspace_id=WS, project_id=PR, version_ids=[version.id])
        )[version.id]
    )
    known = await processing.known_object_keys(workspace_id=WS, project_id=PR)
    assert version.object_key in known and thumbnail.object_key in known
    assert (WS, PR) in await processing.projects_with_media()


@pytest.mark.asyncio
async def test_failure_retry_and_the_copies_of_a_retired_picture(stack) -> None:
    media, processing, runner, objects, _factory, processor = stack
    processor.failures = ["took_too_long"]
    _revision, link, version = await _upload(media, 1)
    await runner.run_pending()
    failed = await processing.get_processing(workspace_id=WS, project_id=PR, version_id=version.id)
    assert failed is not None and failed.status is ProcessingStatus.FAILED
    assert failed.failure_code == "took_too_long"
    assert (await processing.lag())["failed"] >= 1
    pending = await processing.pending_versions(
        processor_version="pictures-1",
        content_types=["image/png"],
        workspace_id=WS,
        include_failed=True,
    )
    assert [item.asset_version_id for item in pending] == [version.id]
    assert pending[0].actor_user_id == "usr_pics"

    await media.retry_processing(SCOPE, WS, PR, link.asset_id, version.id)
    await runner.run_pending()
    ready = await processing.get_processing(workspace_id=WS, project_id=PR, version_id=version.id)
    assert ready is not None and ready.status is ProcessingStatus.READY and ready.attempts == 2

    await media.retire_version(SCOPE, WS, PR, version.id)
    assert (WS, PR, version.id) in await processing.retired_with_variants()
    report = await media.remove_retired_copies(dry_run=False)
    assert report.retired_copies_removed >= 2
    assert await processing.variant_object_keys(workspace_id=WS, project_id=PR) == set()
    assert objects.stat(version.object_key, workspace_id=WS, project_id=PR) is not None


@pytest.mark.asyncio
async def test_orphans_and_regions(stack) -> None:
    media, processing, runner, objects, _factory, _processor = stack
    _revision, link, version = await _upload(media, 1)
    await runner.run_pending()
    # Only picture files are the media service's to remove; a stray non-picture file stays.
    stray = objects.put(b"stray", workspace_id=WS, project_id=PR, content_type="image/png")
    other = objects.put(b"other", workspace_id=WS, project_id=PR)
    report = await media.reconcile_orphans(
        workspace_id=WS, project_id=PR, dry_run=False, grace=timedelta(0)
    )
    assert report.orphans_removed == 1
    assert objects.stat(stray.key, workspace_id=WS, project_id=PR) is None
    assert objects.stat(version.object_key, workspace_id=WS, project_id=PR) is not None
    assert objects.stat(other.key, workspace_id=WS, project_id=PR) is not None

    region = await media.add_region(
        SCOPE, WS, PR, link.asset_id, version.id, x=0, y=0, width=5, height=5, label="Hand"
    )
    assert [r.id for r in await processing.list_regions(workspace_id=WS, project_id=PR)] == [
        region.id
    ]
    # Put back again from an archive: the same row, not a second one.
    await media.restore_regions(WS, PR, await media.export_regions(WS, PR))
    assert len(await processing.list_regions(workspace_id=WS, project_id=PR)) == 1
    retired = await processing.retire_segment(workspace_id=WS, project_id=PR, segment_id=region.id)
    assert retired is not None and retired.retired_at is not None
