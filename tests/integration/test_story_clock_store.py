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
