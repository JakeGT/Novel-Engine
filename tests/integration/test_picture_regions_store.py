"""Marked parts with their entry, writing read in a picture, region-anchored notes and
the index's view of parts, against PostgreSQL (migration 0060_picture_regions)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.adapters.embedding_generations import PgEmbeddingIndexStore
from backend.adapters.observation_store import PostgresObservationStore
from backend.adapters.studio_store import PostgresStudioStore
from backend.domain.media_observations import (
    MediaObservation,
    ObservationKind,
    ObservationState,
    Region,
)
from backend.domain.studio import AssistantFinding, PictureAnchor
from tests.integration.test_media_processing_store import PR, SCOPE, WS, _upload, stack

pytestmark = pytest.mark.integration

__all__ = ["stack"]


@pytest.mark.asyncio
async def test_a_marked_part_keeps_its_entry_and_the_index_reads_it_with_accepted_notes(
    stack, disposable_database
) -> None:
    media, processing, runner, _objects, factory, _processor = stack
    _revision, link, version = await _upload(media, 1)
    await runner.run_pending()
    part = await media.add_region(
        SCOPE,
        WS,
        PR,
        link.asset_id,
        version.id,
        x=64,
        y=48,
        width=128,
        height=96,
        label="The inn sign",
        entity_id="ent_pic",
    )
    assert part.entity_id == "ent_pic"
    stored = await processing.get_segment(workspace_id=WS, project_id=PR, segment_id=part.id)
    assert stored is not None and stored.entity_id == "ent_pic"
    moved = await media.update_region(
        SCOPE,
        WS,
        PR,
        link.asset_id,
        version.id,
        part.id,
        x=0,
        y=0,
        width=320,
        height=240,
        label="The sign",
        entity_id="ent_pic",
    )
    assert (moved.x, moved.width, moved.label) == (0, 320, "The sign")
    assert moved.content_hash != part.content_hash
    unnamed = await media.add_region(
        SCOPE, WS, PR, link.asset_id, version.id, x=400, y=300, width=10, height=10
    )

    observations = PostgresObservationStore(factory)
    await observations.add(
        [
            MediaObservation(
                workspace_id=WS,
                project_id=PR,
                id="obs_writing",
                asset_id=link.asset_id,
                asset_version_id=version.id,
                segment_id=part.id,
                kind=ObservationKind.TEXT_IN_PICTURE,
                text="THE BLACK SWAN",
                region=Region(x=0.05, y=0.05, width=0.2, height=0.1),
                state=ObservationState.CORRECTED,
                corrected_text="The Black Swan",
            ),
            MediaObservation(
                workspace_id=WS,
                project_id=PR,
                id="obs_waiting",
                asset_id=link.asset_id,
                asset_version_id=version.id,
                segment_id=part.id,
                kind=ObservationKind.CAPTION,
                text="Never accepted.",
            ),
        ]
    )
    found = await observations.get(workspace_id=WS, project_id=PR, observation_id="obs_writing")
    assert found is not None and found.kind is ObservationKind.TEXT_IN_PICTURE

    index = PgEmbeddingIndexStore(disposable_database.psycopg_dsn)
    (source,) = index.regions_ready(workspace_id=WS, project_id=PR)
    assert source.segment_id == part.id and source.box == (0.0, 0.0, 0.5, 0.5)
    assert source.label == "The sign" and source.entity_name == "Mara"
    assert source.notes == ("The Black Swan",)
    assert source.region_hash == moved.content_hash
    assert unnamed.id not in {
        item.segment_id for item in index.regions_ready(workspace_id=WS, project_id=PR)
    }

    await media.retire_region(SCOPE, WS, PR, link.asset_id, version.id, part.id)
    assert index.regions_ready(workspace_id=WS, project_id=PR) == []

    async with factory() as session:
        with pytest.raises(Exception):  # noqa: B017,PT011 - the CHECK refuses another kind
            await session.execute(
                text(
                    "INSERT INTO media_observations (workspace_id, project_id, id, asset_id, "
                    "asset_version_id, kind, text) VALUES ('w', 'p', 'o', 'a', 'v', 'ocr', 't')"
                )
            )


@pytest.mark.asyncio
async def test_a_note_keeps_the_part_of_the_picture_it_points_at(stack) -> None:
    *_rest, factory, _processor = stack
    studio = PostgresStudioStore(factory)
    anchor = PictureAnchor(
        asset_id="ast_1",
        version_id="ver_1",
        label="“Mara”, version 1",
        segment_id="seg_1",
        region=(0.5, 0.2, 0.1, 0.1),
        part_label="Her coat",
    )
    await studio.add_findings(
        workspace_id=WS,
        project_id=PR,
        findings=[
            AssistantFinding(
                id="fnd_pic",
                target_kind="draft",
                target_id="txt_1",
                job_id="job_1",
                category="pictures",
                message="Her picture shows a blue coat.",
                suggestion="",
                start=0,
                end=4,
                check_pass="pictures",
                picture_anchor=anchor,
            ),
            AssistantFinding(
                id="fnd_plain",
                target_kind="draft",
                target_id="txt_1",
                job_id="job_1",
                category="voice",
                message="Too long.",
                suggestion="",
                start=5,
                end=8,
                check_pass="voice",
            ),
        ],
    )
    notes = {
        note.id: note
        for note in await studio.findings(
            workspace_id=WS, project_id=PR, target_kind="draft", target_id="txt_1"
        )
    }
    assert notes["fnd_pic"].picture_anchor == anchor
    assert notes["fnd_plain"].picture_anchor is None
