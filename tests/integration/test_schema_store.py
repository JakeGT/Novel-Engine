import pytest
import pytest_asyncio

from backend.adapters.schema_store import PostgresSchemaVersionStore
from backend.core.database import create_engine, create_session_factory

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def store(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresSchemaVersionStore(create_session_factory(engine))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_draft_publish_and_project_isolation(store) -> None:
    definition = {"label": "x", "entity_extensions": [], "predicate_extensions": []}

    first = await store.create_draft(
        workspace_id="ws",
        project_id="p1",
        definition=definition,
        content_hash="hash-1",
        compatibility="additive",
        created_by_user_id="u1",
    )
    assert first.version == 1
    assert first.status.value == "draft"

    second = await store.create_draft(
        workspace_id="ws",
        project_id="p1",
        definition=definition,
        content_hash="hash-1",
        compatibility="additive",
        created_by_user_id="u1",
    )
    assert second.version == 2

    published = await store.publish(workspace_id="ws", project_id="p1", version_id=second.id)
    assert published is not None
    assert published.status.value == "published"
    assert published.published_at is not None

    current = await store.get_published(workspace_id="ws", project_id="p1")
    assert current is not None and current.id == second.id
    assert await store.get_published(workspace_id="ws", project_id="p2") is None

    versions = await store.list(workspace_id="ws", project_id="p1")
    assert [v.version for v in versions] == [2, 1]

    # Publishing an already-published version is a no-op.
    assert await store.publish(workspace_id="ws", project_id="p1", version_id=second.id) is None
