"""Preparing pictures: the job, its idempotency, retry, quota, retention and archives
(AST-003, AST-008, OPS-007). The decoder is a stand-in; the real one has its own tests."""

from __future__ import annotations

import asyncio
import io
from datetime import timedelta

import pytest
from PIL import Image

from backend.adapters.image_processor import PROCESSOR_VERSION
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.jobs import JobStatus
from backend.domain.media import ProcessingStatus, StorageQuotaExceeded, VariantKind
from backend.domain.operations import CreateEntityOp
from backend.services.job_runner import JobRunner
from backend.services.media_service import MEDIA_PROCESS_KIND, MediaService
from backend.services.mutation_service import MutationService
from tests.doubles import (
    FakeImageProcessor,
    InMemoryCanonJournal,
    InMemoryCanonRepository,
    InMemoryJobStore,
    InMemoryMediaProcessingStore,
    InMemoryMediaStore,
    InMemoryObjectStore,
)

pytestmark = pytest.mark.application

WS, PR = "ws_pics", "pr_pics"
SCOPE = AccessScope(actor_user_id="usr_1", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR)


def _png(color: str = "teal", size: tuple[int, int] = (40, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


class Stack:
    def __init__(self, *, quota: int = 2 * 1024**3, processor: bool = True) -> None:
        self.media = InMemoryMediaStore()
        self.objects = InMemoryObjectStore()
        self.journal = InMemoryCanonJournal()
        self.canon = InMemoryCanonRepository(journal=self.journal, media=self.media)
        self.mutation = MutationService(self.journal, self.canon, local_mode=True)
        self.jobs = InMemoryJobStore()
        self.runner = JobRunner(
            store=self.jobs, journal=self.journal, local_mode=True, autostart=False
        )
        self.processing = InMemoryMediaProcessingStore(self.media)
        self.processor = FakeImageProcessor()
        self.service = MediaService(
            media=self.media,
            objects=self.objects,
            mutation=self.mutation,
            reader=self.canon,
            journal=self.journal,
            local_mode=True,
            processing=self.processing,
            processor=self.processor if processor else None,
            runner=self.runner,
            quota_bytes=quota,
        )
        self.service.register_jobs()
        self.revision = 0

    async def entry(self, entity_id: str = "ent_mara") -> str:
        draft = await self.mutation.create_draft(
            SCOPE,
            workspace_id=WS,
            project_id=PR,
            operations=[CreateEntityOp(id=entity_id, type="Character", name="Mara")],
            expected_head=self.revision,
        )
        self.revision = (await self.mutation.commit(SCOPE, draft.id)).revision
        return entity_id

    async def upload(self, data: bytes | None = None, entity_id: str = "ent_mara"):
        result, link, version = await self.service.add_picture(
            SCOPE,
            WS,
            PR,
            entity_id=entity_id,
            data=data or _png(),
            name=None,
            expected_revision=self.revision,
        )
        self.revision = result.revision
        return link, version

    def media_jobs(self):
        return [job for job in self.jobs.jobs.values() if job.kind == MEDIA_PROCESS_KIND]


@pytest.mark.asyncio
async def test_an_upload_is_queued_and_prepared_into_copies_and_a_whole_picture() -> None:
    stack = Stack()
    await stack.entry()
    link, version = await stack.upload()

    [job] = stack.media_jobs()
    assert job.status is JobStatus.QUEUED
    assert job.payload["asset_version_id"] == version.id
    assert job.payload["processor_version"] == PROCESSOR_VERSION
    queued = await stack.service.processing_status(SCOPE, WS, PR, link.asset_id, version.id)
    assert queued.status is ProcessingStatus.QUEUED

    [finished] = await stack.runner.run_pending()
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.result == {"outcome": "ready", "width": 640, "height": 480, "copies": 2}

    view = await stack.service.processing_status(SCOPE, WS, PR, link.asset_id, version.id)
    assert view.status is ProcessingStatus.READY and view.current
    assert view.copies == ("thumbnail", "web") and (view.width, view.height) == (640, 480)
    [whole] = await stack.service.list_segments(SCOPE, WS, PR, link.asset_id, version.id)
    assert (whole.kind.value, whole.x, whole.y, whole.width, whole.height) == (
        "whole",
        0,
        0,
        640,
        480,
    )
    assert whole.content_hash == version.checksum

    data, content_type, served = await stack.service.open_picture(
        SCOPE, WS, PR, link.asset_id, version.id, copy=VariantKind.THUMBNAIL
    )
    assert (content_type, served) == ("image/webp", "thumbnail") and data.startswith(b"RIFF")
    library = await stack.service.picture_library(SCOPE, WS, PR)
    assert library[0].has_thumbnail and library[0].processing.status is ProcessingStatus.READY


@pytest.mark.asyncio
async def test_before_a_copy_is_made_the_original_is_served() -> None:
    stack = Stack()
    await stack.entry()
    link, version = await stack.upload()
    original = await stack.service.open_picture(
        SCOPE, WS, PR, link.asset_id, version.id, copy=VariantKind.THUMBNAIL
    )
    assert original[1:] == ("image/png", "original")


@pytest.mark.asyncio
async def test_preparing_again_changes_nothing() -> None:
    stack = Stack()
    await stack.entry()
    link, version = await stack.upload()
    await stack.runner.run_pending()
    keys_before = await stack.processing.variant_object_keys(workspace_id=WS, project_id=PR)
    objects_before = len(stack.objects.list_objects(workspace_id=WS, project_id=PR))

    again = await stack.service.process_version(WS, PR, version.id)
    assert again == {"outcome": "already_done"}
    assert stack.processor.calls == 1
    # Asking for a retry of a picture already prepared queues nothing.
    view = await stack.service.retry_processing(SCOPE, WS, PR, link.asset_id, version.id)
    assert view.status is ProcessingStatus.READY and len(stack.media_jobs()) == 1
    # And the backfill finds nothing to do.
    report = await stack.service.queue_backfill()
    assert report.queued == 0
    assert await stack.processing.variant_object_keys(workspace_id=WS, project_id=PR) == keys_before
    assert len(stack.objects.list_objects(workspace_id=WS, project_id=PR)) == objects_before


@pytest.mark.asyncio
async def test_a_failure_is_told_plainly_and_can_be_retried() -> None:
    stack = Stack()
    await stack.entry()
    stack.processor.failures = ["took_too_long"]
    link, version = await stack.upload()
    [finished] = await stack.runner.run_pending()
    # The job did its work; the picture's preparation is what failed.
    assert finished.result == {"outcome": "failed", "failure_code": "took_too_long"}

    failed = await stack.service.processing_status(SCOPE, WS, PR, link.asset_id, version.id)
    assert failed.status is ProcessingStatus.FAILED and failed.retryable
    assert failed.message == "Opening this picture took too long. Try again later."
    # Nothing half-made is left: no copies were stored.
    assert await stack.processing.variant_object_keys(workspace_id=WS, project_id=PR) == set()
    # The original is still shown in the meantime.
    shown = await stack.service.open_picture(
        SCOPE, WS, PR, link.asset_id, version.id, copy=VariantKind.WEB
    )
    assert shown[2] == "original"

    retried = await stack.service.retry_processing(SCOPE, WS, PR, link.asset_id, version.id)
    assert retried.status is ProcessingStatus.QUEUED
    # Asking twice queues it once.
    await stack.service.retry_processing(SCOPE, WS, PR, link.asset_id, version.id)
    assert len([job for job in stack.media_jobs() if not job.status.finished]) == 1
    await stack.runner.run_pending()
    ready = await stack.service.processing_status(SCOPE, WS, PR, link.asset_id, version.id)
    assert ready.status is ProcessingStatus.READY and ready.attempts == 2


@pytest.mark.asyncio
async def test_a_changed_original_is_found_damaged_not_trusted() -> None:
    stack = Stack()
    await stack.entry()
    link, version = await stack.upload()
    key = (WS, PR, version.object_key)
    data, stat = stack.objects._objects[key]
    stack.objects._objects[key] = (data + b"tampered", stat)
    [finished] = await stack.runner.run_pending()
    assert finished.result == {"outcome": "failed", "failure_code": "damaged"}
    assert stack.processor.calls == 0


@pytest.mark.asyncio
async def test_processing_jobs_never_count_toward_a_persons_limit() -> None:
    stack = Stack()
    await stack.entry()
    for _ in range(5):
        await stack.upload()
    assert len(stack.media_jobs()) == 5
    assert await stack.jobs.count_active(workspace_id=WS, project_id=PR, actor_user_id="usr_1") == 0


@pytest.mark.asyncio
async def test_an_upload_over_the_workspace_room_is_refused_in_plain_words() -> None:
    picture = _png()
    stack = Stack(quota=len(picture) * 2)
    await stack.entry()
    await stack.upload(picture)
    with pytest.raises(StorageQuotaExceeded) as refused:
        await stack.upload(_png("red"))
    assert refused.value.status_code == 413
    assert "no room left for this picture" in str(refused.value)
    assert "Remove pictures you no longer need" in str(refused.value)
    # Nothing of the refused picture was kept.
    assert len(await stack.media.list_assets(workspace_id=WS, project_id=PR)) == 1


@pytest.mark.asyncio
async def test_no_limit_when_the_quota_is_zero() -> None:
    stack = Stack(quota=0)
    await stack.entry()
    await stack.upload()
    await stack.upload(_png("red"))
    assert len(await stack.media.list_assets(workspace_id=WS, project_id=PR)) == 2


@pytest.mark.asyncio
async def test_orphaned_files_are_found_after_the_grace_period_and_removed_on_request() -> None:
    stack = Stack()
    await stack.entry()
    link, version = await stack.upload()
    await stack.runner.run_pending()
    stray = stack.objects.put(
        b"left behind", workspace_id=WS, project_id=PR, content_type="image/png"
    )

    # Still within the grace period: an upload on its way is never touched.
    fresh = await stack.service.reconcile_orphans(dry_run=False)
    assert fresh.orphans_found == 0

    counted = await stack.service.reconcile_orphans(dry_run=True, grace=timedelta(0))
    assert (counted.orphans_found, counted.orphans_removed) == (1, 0)
    assert stack.objects.stat(stray.key, workspace_id=WS, project_id=PR) is not None

    removed = await stack.service.reconcile_orphans(dry_run=False, grace=timedelta(0))
    assert (removed.orphans_found, removed.orphans_removed) == (1, 1)
    assert removed.orphan_bytes == len(b"left behind")
    assert stack.objects.stat(stray.key, workspace_id=WS, project_id=PR) is None
    # The picture and its copies are kept.
    keys = {stat.key for stat in stack.objects.list_objects(workspace_id=WS, project_id=PR)}
    assert version.object_key in keys
    assert await stack.processing.variant_object_keys(workspace_id=WS, project_id=PR) <= keys


@pytest.mark.asyncio
async def test_only_picture_files_are_treated_as_left_behind() -> None:
    # Other kinds in the project's store (a seeded drawing, an export) are not this
    # service's to remove, however old and unreferenced.
    stack = Stack()
    await stack.entry()
    await stack.upload()
    await stack.runner.run_pending()
    drawing = stack.objects.put(
        b"<svg/>", workspace_id=WS, project_id=PR, content_type="image/svg+xml"
    )
    export = stack.objects.put(b"{}", workspace_id=WS, project_id=PR)

    report = await stack.service.reconcile_orphans(dry_run=False, grace=timedelta(0))

    assert (report.orphans_found, report.orphans_removed) == (0, 0)
    for kept in (drawing, export):
        assert stack.objects.stat(kept.key, workspace_id=WS, project_id=PR) is not None


class _SlowCounting(InMemoryMediaProcessingStore):
    """Counting the room lets other work run before the answer arrives, as a database
    query does."""

    async def workspace_bytes(self, *, workspace_id):
        used = await super().workspace_bytes(workspace_id=workspace_id)
        await asyncio.sleep(0.05)
        return used


@pytest.mark.asyncio
async def test_two_uploads_at_once_cannot_both_take_the_last_room() -> None:
    first, second = _png("red"), _png("blue")
    # Room for one picture (with its copies) but not two.
    stack = Stack(quota=max(len(first), len(second)) * 2)
    stack.processing = _SlowCounting(stack.media)
    stack.service._processing = stack.processing
    await stack.entry()
    await stack.entry("ent_tam")

    outcomes = await asyncio.gather(
        stack.service.add_picture(
            SCOPE, WS, PR, entity_id="ent_mara", data=first, name=None, expected_revision=2
        ),
        stack.service.add_picture(
            SCOPE, WS, PR, entity_id="ent_tam", data=second, name=None, expected_revision=2
        ),
        return_exceptions=True,
    )

    refused = [o for o in outcomes if isinstance(o, StorageQuotaExceeded)]
    assert len(refused) == 1, outcomes
    assert len(await stack.media.list_assets(workspace_id=WS, project_id=PR)) == 1


@pytest.mark.asyncio
async def test_registering_a_version_counts_against_the_room_too() -> None:
    picture = _png()
    stack = Stack(quota=len(picture) * 2)
    await stack.entry()
    await stack.upload(picture)
    asset = await stack.service.register_asset(SCOPE, WS, PR, name="Map")
    stored = stack.objects.put(
        _png("red", (60, 40)), workspace_id=WS, project_id=PR, content_type="image/png"
    )

    with pytest.raises(StorageQuotaExceeded):
        await stack.service.register_version(SCOPE, WS, PR, asset.id, object_key=stored.key)
    assert await stack.media.list_versions(workspace_id=WS, project_id=PR, asset_id=asset.id) == []


@pytest.mark.asyncio
async def test_copies_of_a_retired_picture_are_removed_and_its_original_kept() -> None:
    stack = Stack()
    await stack.entry()
    link, version = await stack.upload()
    await stack.runner.run_pending()
    removed_hook: list[tuple[str, str, list[str]]] = []

    async def forget(workspace_id: str, project_id: str, version_ids: list[str]) -> None:
        removed_hook.append((workspace_id, project_id, version_ids))

    stack.service.on_versions_removed(forget)
    await stack.service.retire_version(SCOPE, WS, PR, version.id)
    # The cleanup hook for derived indexes heard of it at once.
    assert removed_hook == [(WS, PR, [version.id])]

    counted = await stack.service.remove_retired_copies(dry_run=True)
    assert counted.retired_copies_removed == 2
    done = await stack.service.remove_retired_copies(dry_run=False)
    assert done.retired_copies_removed == 2
    assert await stack.processing.variant_object_keys(workspace_id=WS, project_id=PR) == set()
    assert stack.objects.stat(version.object_key, workspace_id=WS, project_id=PR) is not None
    # Nothing left to do the second time.
    assert (await stack.service.remove_retired_copies(dry_run=False)).retired_copies_removed == 0


@pytest.mark.asyncio
async def test_a_failing_cleanup_hook_never_undoes_a_retirement() -> None:
    stack = Stack()
    await stack.entry()
    link, version = await stack.upload()

    async def broken(*_args: object) -> None:
        raise RuntimeError("vector store down")

    stack.service.on_versions_removed(broken)
    retired = await stack.service.retire_version(SCOPE, WS, PR, version.id)
    assert retired.retired_revision is not None


@pytest.mark.asyncio
async def test_the_backfill_queues_pictures_from_before_processing() -> None:
    stack = Stack(processor=False)
    await stack.entry()
    await stack.upload()
    await stack.upload(_png("red"))
    assert stack.media_jobs() == []  # nothing queued while processing is off

    stack.service._processor = stack.processor
    stack.service.register_jobs()
    report = await stack.service.queue_backfill(local_actor="usr_1")
    assert report.queued == 2
    assert len(stack.media_jobs()) == 2
    # Queued once: asking again finds them waiting already.
    assert (await stack.service.queue_backfill(local_actor="usr_1")).queued == 0
    await stack.runner.run_pending()
    lag = await stack.service.processing_lag()
    assert lag == {"waiting": 0, "oldest_seconds": None, "failed": 0}


@pytest.mark.asyncio
async def test_regions_are_marked_inside_a_prepared_picture_only() -> None:
    stack = Stack()
    await stack.entry()
    link, version = await stack.upload()
    with pytest.raises(ValidationError, match="once the picture is ready"):
        await stack.service.add_region(
            SCOPE, WS, PR, link.asset_id, version.id, x=0, y=0, width=10, height=10
        )
    await stack.runner.run_pending()
    region = await stack.service.add_region(
        SCOPE, WS, PR, link.asset_id, version.id, x=10, y=20, width=100, height=50, label=" Face "
    )
    assert region.kind.value == "region" and region.label == "Face"
    with pytest.raises(ValidationError, match="past the picture's edge"):
        await stack.service.add_region(
            SCOPE, WS, PR, link.asset_id, version.id, x=600, y=0, width=100, height=10
        )
    segments = await stack.service.list_segments(SCOPE, WS, PR, link.asset_id, version.id)
    assert [segment.kind.value for segment in segments] == ["whole", "region"]

    exported = await stack.service.export_regions(WS, PR)
    assert [item["id"] for item in exported] == [region.id]

    await stack.service.retire_region(SCOPE, WS, PR, link.asset_id, version.id, region.id)
    assert len(await stack.service.list_segments(SCOPE, WS, PR, link.asset_id, version.id)) == 1
    with pytest.raises(NotFoundError):
        await stack.service.retire_region(SCOPE, WS, PR, link.asset_id, version.id, region.id)

    # Restored from an archive: only for a picture that is there.
    restored = await stack.service.restore_regions(
        WS, PR, [*exported, {**exported[0], "id": "seg_x", "asset_version_id": "asv_gone"}, "bad"]
    )
    assert restored == 1
