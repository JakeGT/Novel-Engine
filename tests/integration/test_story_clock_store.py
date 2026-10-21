"""The author's travel times and lengths against PostgreSQL (CON-003; migration 0047)."""

import pytest
import pytest_asyncio

from backend.adapters.story_clock import PostgresStoryClockStore
from backend.core.database import create_engine, create_session_factory

pytestmark = pytest.mark.integration

WS, PR = "ws_how_long", "pr_how_long"
TABLE = {"travel": [{"from_id": "a", "to_id": "b", "amount": 2}], "lasting": []}


@pytest_asyncio.fixture
async def clocks(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresStoryClockStore(create_session_factory(engine))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_table_is_saved_with_a_version_and_a_stale_save_is_refused(clocks) -> None:
    assert await clocks.get_how_long(workspace_id=WS, project_id=PR) == ({}, 0)
    assert (
        await clocks.set_how_long(workspace_id=WS, project_id=PR, table=TABLE, expected_version=0)
        == 1
    )
    assert await clocks.get_how_long(workspace_id=WS, project_id=PR) == (TABLE, 1)
    # Someone else saved first: nothing changes.
    assert (
        await clocks.set_how_long(workspace_id=WS, project_id=PR, table={}, expected_version=0)
        is None
    )
    assert (
        await clocks.set_how_long(workspace_id=WS, project_id=PR, table={}, expected_version=5)
        is None
    )
    assert (
        await clocks.set_how_long(
            workspace_id=WS, project_id=PR, table={"travel": []}, expected_version=1
        )
        == 2
    )
    # The unit is kept beside it, and a story with only a unit starts at version 0.
    assert await clocks.get_unit(workspace_id=WS, project_id=PR) == "year"
    await clocks.set_unit(workspace_id=WS, project_id="other", unit="day")
    assert await clocks.get_how_long(workspace_id=WS, project_id="other") == ({}, 0)
    assert (
        await clocks.set_how_long(
            workspace_id=WS, project_id="other", table=TABLE, expected_version=0
        )
        == 1
    )
    assert await clocks.get_unit(workspace_id=WS, project_id="other") == "day"


SCALES = {"scales": [{"id": "trust", "name": "Trust", "levels": ["None", "Some"], "ordered": True}]}


@pytest.mark.asyncio
async def test_the_shared_scales_are_saved_with_their_own_version(clocks) -> None:
    """STY-006, migration 0051: the scales sit beside the unit and the travel table, each
    with its own version, and a stale save is refused."""
    ws, pr = "ws_scales", "pr_scales"
    assert await clocks.get_scales(workspace_id=ws, project_id=pr) == ({}, 0)
    assert (
        await clocks.set_scales(workspace_id=ws, project_id=pr, scales=SCALES, expected_version=0)
        == 1
    )
    assert await clocks.get_scales(workspace_id=ws, project_id=pr) == (SCALES, 1)
    assert (
        await clocks.set_scales(workspace_id=ws, project_id=pr, scales={}, expected_version=0)
        is None
    )
    assert (
        await clocks.set_scales(workspace_id=ws, project_id=pr, scales={}, expected_version=3)
        is None
    )
    assert (
        await clocks.set_scales(workspace_id=ws, project_id=pr, scales={}, expected_version=1) == 2
    )
    # A story that already has a unit and a travel table starts its scales at version 0,
    # and saving them leaves the others alone.
    await clocks.set_unit(workspace_id=ws, project_id="other", unit="day")
    await clocks.set_how_long(workspace_id=ws, project_id="other", table=TABLE, expected_version=0)
    assert await clocks.get_scales(workspace_id=ws, project_id="other") == ({}, 0)
    assert (
        await clocks.set_scales(
            workspace_id=ws, project_id="other", scales=SCALES, expected_version=0
        )
        == 1
    )
    assert await clocks.get_unit(workspace_id=ws, project_id="other") == "day"
    assert await clocks.get_how_long(workspace_id=ws, project_id="other") == (TABLE, 1)
