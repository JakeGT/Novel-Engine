"""Two servers delivering the same story never move its graph watermark backwards (ADR-012)."""

from __future__ import annotations

import pytest

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.identity import utcnow
from backend.domain.projection import ProjectionStatus, ProjectionWatermark
from backend.services.projection_service import PROJECTION_SCHEMA_VERSION

pytestmark = pytest.mark.integration

WORKSPACE = "ws_wm_it"
PROJECT = "pr_wm_it"


@pytest.mark.asyncio
async def test_the_watermark_never_moves_backwards_except_for_a_rebuild(
    disposable_database,
) -> None:
    engine = create_engine(disposable_database.settings())
    repository = PostgresCanonRepository(create_session_factory(engine))
    target = {
        "projection": "graph",
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "branch_id": "br_wm_it",
    }

    async def mark(
        revision: int, status: ProjectionStatus, schema: str = PROJECTION_SCHEMA_VERSION
    ) -> tuple[int, ProjectionStatus]:
        await repository.set_watermark(
            ProjectionWatermark(
                revision=revision,
                projection_schema_version=schema,
                status=status,
                updated_at=utcnow(),
                **target,
            )
        )
        stored = await repository.get_watermark(**target)
        assert stored is not None
        return stored.revision, stored.status

    try:
        assert await mark(5, ProjectionStatus.CURRENT) == (5, ProjectionStatus.CURRENT)
        # A slower process finishing revision 3, or failing at it, leaves revision 5 alone.
        assert await mark(3, ProjectionStatus.CURRENT) == (5, ProjectionStatus.CURRENT)
        assert await mark(3, ProjectionStatus.ERROR) == (5, ProjectionStatus.CURRENT)
        # A failure at the same place is recorded.
        assert await mark(5, ProjectionStatus.ERROR) == (5, ProjectionStatus.ERROR)
        assert await mark(7, ProjectionStatus.CURRENT) == (7, ProjectionStatus.CURRENT)
        # A rebuild starts again from nothing on purpose, and so does a new layout.
        assert await mark(0, ProjectionStatus.REBUILDING) == (0, ProjectionStatus.REBUILDING)
        assert await mark(2, ProjectionStatus.CURRENT) == (2, ProjectionStatus.CURRENT)
        assert await mark(1, ProjectionStatus.CURRENT, schema="0") == (1, ProjectionStatus.CURRENT)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_one_process_at_a_time_delivers_a_story(disposable_database) -> None:
    engine = create_engine(disposable_database.settings())
    journal = PostgresCanonJournal(create_session_factory(engine))
    story = {"workspace_id": WORKSPACE, "project_id": PROJECT}
    try:
        async with journal.story_lock(**story) as first:
            assert first is True
            # A second holder (another server) is turned away at once, without waiting.
            async with journal.story_lock(**story) as second:
                assert second is False
            # Another story is not held up.
            async with journal.story_lock(workspace_id=WORKSPACE, project_id="pr_other") as other:
                assert other is True
        # Let go when the block ends, also when it fails.
        with pytest.raises(RuntimeError):
            async with journal.story_lock(**story) as again:
                assert again is True
                raise RuntimeError("drain failed")
        async with journal.story_lock(**story) as after:
            assert after is True
    finally:
        await engine.dispose()
