"""Marking, changing and taking off a part of a picture (AST-007, CTX-012): the search
index is brought up to date by a queued job, never inside the author's request, with one
waiting per picture at a time; a picture holds at most 50 parts; parts restored from an
archive are bounded and keep only links to entries still in the story."""

from __future__ import annotations

import pytest

from backend.domain.errors import ValidationError
from backend.domain.jobs import UNCOUNTED_KINDS, JobStatus
from backend.services.embedding_index import (
    REGION_REFRESH_KIND,
    EmbeddingIndexService,
    wire_media,
)
from backend.services.media_service import MAX_REGIONS_PER_VERSION
from tests.application.test_media_processing import PR, SCOPE, WS, Stack

pytestmark = [pytest.mark.application, pytest.mark.asyncio]


class _Index(EmbeddingIndexService):
    """The index service with the refresh itself counted instead of done."""

    def __init__(self, runner: object) -> None:
        super().__init__(index=None, embedder=None, runner=runner)
        self.refreshed: list[tuple[str, str]] = []

    async def regions_changed(self, workspace_id: str, project_id: str) -> int:
        self.refreshed.append((workspace_id, project_id))
        return 0


async def _ready() -> tuple[Stack, _Index, str, str]:
    stack = Stack()
    index = _Index(stack.runner)
    index.register_jobs()
    wire_media(stack.service, index)
    await stack.entry()
    link, version = await stack.upload()
    await stack.runner.run_pending()  # the picture is prepared (640 by 480)
    return stack, index, link.asset_id, version.id


def _refreshes(stack: Stack) -> list:
    return [job for job in stack.jobs.jobs.values() if job.kind == REGION_REFRESH_KIND]


async def test_a_part_changed_queues_one_refresh_per_picture_and_none_runs_in_the_request() -> None:
    stack, index, asset_id, version_id = await _ready()
    part = await stack.service.add_region(
        SCOPE, WS, PR, asset_id, version_id, x=10, y=10, width=50, height=50, label="Face"
    )
    assert index.refreshed == []  # nothing embedded inside the request
    [job] = _refreshes(stack)
    assert job.status is JobStatus.QUEUED and job.payload == {"asset_id": asset_id}
    assert REGION_REFRESH_KIND in UNCOUNTED_KINDS  # never counts toward the author's limit

    # More changes to the same picture before it starts are caught by the same job.
    await stack.service.update_region(
        SCOPE, WS, PR, asset_id, version_id, part.id, x=20, y=20, width=50, height=50
    )
    await stack.service.add_region(
        SCOPE, WS, PR, asset_id, version_id, x=100, y=100, width=10, height=10, label="Hand"
    )
    assert len(_refreshes(stack)) == 1

    await stack.runner.run_pending()
    assert index.refreshed == [(WS, PR)]
    assert _refreshes(stack)[0].status is JobStatus.SUCCEEDED

    # Once it has started, the next change needs (and gets) a refresh of its own.
    await stack.service.retire_region(SCOPE, WS, PR, asset_id, version_id, part.id)
    assert [job.status for job in _refreshes(stack)].count(JobStatus.QUEUED) == 1
    await stack.runner.run_pending()
    assert index.refreshed == [(WS, PR), (WS, PR)]


async def test_a_picture_holds_at_most_fifty_parts() -> None:
    stack, _index, asset_id, version_id = await _ready()
    for n in range(MAX_REGIONS_PER_VERSION):
        await stack.service.add_region(
            SCOPE, WS, PR, asset_id, version_id, x=n, y=0, width=1, height=1, label=f"p{n}"
        )
    with pytest.raises(ValidationError, match="at most 50 marked parts"):
        await stack.service.add_region(
            SCOPE, WS, PR, asset_id, version_id, x=0, y=10, width=1, height=1
        )
    # Taking one off makes room again.
    first = next(
        segment
        for segment in await stack.service.list_segments(SCOPE, WS, PR, asset_id, version_id)
        if segment.kind.value == "region"
    )
    await stack.service.retire_region(SCOPE, WS, PR, asset_id, version_id, first.id)
    await stack.service.add_region(
        SCOPE, WS, PR, asset_id, version_id, x=0, y=10, width=1, height=1
    )


async def test_parts_from_an_archive_are_bounded_and_drop_links_to_entries_gone() -> None:
    stack, _index, asset_id, version_id = await _ready()
    base = {
        "asset_id": asset_id,
        "asset_version_id": version_id,
        "x": 1,
        "y": 1,
        "width": 10,
        "height": 10,
        "label": "Face",
    }
    restored = await stack.service.restore_regions(
        WS,
        PR,
        [
            {**base, "id": "seg_kept", "entity_id": "ent_mara"},
            {**base, "id": "seg_unlinked", "entity_id": "ent_gone"},
            # Far past any picture's size (and the database's numbers): skipped, not fatal.
            {**base, "id": "seg_huge", "x": 10**30},
            {**base, "id": "seg_wide", "width": 10**12},
            # Inside the bound but outside this prepared picture (640 by 480).
            {**base, "id": "seg_outside", "x": 635},
        ],
    )
    assert restored == 2
    parts = {
        segment.id: segment
        for segment in await stack.service.list_segments(SCOPE, WS, PR, asset_id, version_id)
        if segment.kind.value == "region"
    }
    assert set(parts) == {"seg_kept", "seg_unlinked"}
    assert parts["seg_kept"].entity_id == "ent_mara"
    assert parts["seg_unlinked"].entity_id is None

    # Never past a picture's limit of parts.
    many = [{**base, "id": f"seg_{n}"} for n in range(MAX_REGIONS_PER_VERSION + 5)]
    assert await stack.service.restore_regions(WS, PR, many) == MAX_REGIONS_PER_VERSION - 2
