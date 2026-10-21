"""Deleting a picture's files before its story, against PostgreSQL (AST-008; migration
0050): the version row stays as the tombstone, the audit gains two events, and deleted
versions stop counting toward the workspace's room."""

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaProcessingStore, PostgresMediaStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.media import AssetLifecycle
from backend.services.media_service import MediaService
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryObjectStore

pytestmark = pytest.mark.integration

WS, PR = "ws_gone_it", "pr_gone_it"


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="usr_owner", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
    )


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    repository = PostgresCanonRepository(factory)
    store = PostgresMediaStore(factory)
    objects = InMemoryObjectStore()
    media = MediaService(
        media=store,
        objects=objects,
        mutation=MutationService(journal, repository, local_mode=True),
        reader=repository,
        journal=journal,
        local_mode=True,
        processing=PostgresMediaProcessingStore(factory),
    )
    try:
        yield media, store, objects
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_deleting_a_picture_keeps_its_tombstone(stack) -> None:
    media, store, objects = stack
    stat = objects.put(
        b"\x89PNG portrait", workspace_id=WS, project_id=PR, content_type="image/png"
    )
    asset = await media.register_asset(_scope(), WS, PR, name="Portrait")
    version = await media.register_version(_scope(), WS, PR, asset.id, object_key=stat.key)
    before = await media.storage_used(WS)
    assert before >= version.byte_length

    deleted = await media.delete_picture(_scope(), WS, PR, asset.id)
    assert deleted.files_removed == 1 and deleted.asset.lifecycle is AssetLifecycle.DELETED
    assert objects.stat(stat.key, workspace_id=WS, project_id=PR) is None
    kept = await store.get_version(workspace_id=WS, project_id=PR, version_id=version.id)
    assert kept is not None and kept.deleted_at is not None
    assert kept.checksum == version.checksum and kept.retired_revision is not None
    assert await media.storage_used(WS) == before - version.byte_length
    events = await store.list_lifecycle_events(workspace_id=WS, project_id=PR, asset_id=asset.id)
    assert [event.action.value for event in events][-2:] == ["original_deleted", "asset_deleted"]
    assert events[-1].actor_user_id == "usr_owner"

    # Doing it again changes nothing and records nothing.
    again = await store.delete_original(workspace_id=WS, project_id=PR, version_id=version.id)
    assert again is not None and again.deleted_at == kept.deleted_at
    await store.delete_asset(workspace_id=WS, project_id=PR, asset_id=asset.id)
    assert len(
        await store.list_lifecycle_events(workspace_id=WS, project_id=PR, asset_id=asset.id)
    ) == len(events)


@pytest.mark.asyncio
async def test_a_shared_file_is_counted_once_and_kept_for_the_picture_still_using_it(
    stack,
) -> None:
    media, store, objects = stack
    stat = objects.put(b"\x89PNG shared", workspace_id=WS, project_id=PR, content_type="image/png")
    first = await media.register_asset(_scope(), WS, PR, name="First")
    second = await media.register_asset(_scope(), WS, PR, name="Second")
    await media.register_version(_scope(), WS, PR, first.id, object_key=stat.key)
    once = await media.storage_used(WS)
    kept = await media.register_version(_scope(), WS, PR, second.id, object_key=stat.key)
    assert await media.storage_used(WS) == once
    assert await store.object_keys_in_use(
        workspace_id=WS, project_id=PR, object_keys=[stat.key, "other"], except_asset_id=first.id
    ) == {stat.key}

    deleted = await media.delete_picture(_scope(), WS, PR, first.id)
    assert deleted.files_removed == 0
    assert objects.stat(stat.key, workspace_id=WS, project_id=PR) is not None
    assert await media.storage_used(WS) == once
    still = await store.get_version(workspace_id=WS, project_id=PR, version_id=kept.id)
    assert still is not None and still.deleted_at is None
    assert (
        await store.object_keys_in_use(
            workspace_id=WS, project_id=PR, object_keys=[stat.key], except_asset_id=second.id
        )
        == set()
    )


@pytest.mark.asyncio
async def test_a_hold_marks_a_picture_deleted_and_can_be_put_back(stack) -> None:
    media, store, objects = stack
    asset = await media.register_asset(_scope(), WS, PR, name="Held")
    assert await store.hold_for_deletion(
        workspace_id=WS, project_id=PR, asset_id=asset.id, lifecycle=AssetLifecycle.ACTIVE
    )
    held = await store.get_asset(workspace_id=WS, project_id=PR, asset_id=asset.id)
    assert held is not None and held.lifecycle is AssetLifecycle.DELETED
    # Only from the lifecycle it was seen in.
    assert not await store.hold_for_deletion(
        workspace_id=WS, project_id=PR, asset_id=asset.id, lifecycle=AssetLifecycle.ACTIVE
    )
    await store.release_hold(
        workspace_id=WS, project_id=PR, asset_id=asset.id, lifecycle=AssetLifecycle.ACTIVE
    )
    back = await store.get_asset(workspace_id=WS, project_id=PR, asset_id=asset.id)
    assert back is not None and back.lifecycle is AssetLifecycle.ACTIVE

    # Held, then deleted: recorded once, with a revision like a deleted version's.
    await store.hold_for_deletion(
        workspace_id=WS, project_id=PR, asset_id=asset.id, lifecycle=AssetLifecycle.ACTIVE
    )
    gone = await store.delete_asset(workspace_id=WS, project_id=PR, asset_id=asset.id)
    assert gone is not None and gone.retired_revision == 0
    await store.delete_asset(workspace_id=WS, project_id=PR, asset_id=asset.id)
    actions = [
        event.action.value
        for event in await store.list_lifecycle_events(
            workspace_id=WS, project_id=PR, asset_id=asset.id
        )
    ]
    assert actions.count("asset_deleted") == 1
    # A recorded deletion is never put back.
    await store.release_hold(
        workspace_id=WS, project_id=PR, asset_id=asset.id, lifecycle=AssetLifecycle.ACTIVE
    )
    final = await store.get_asset(workspace_id=WS, project_id=PR, asset_id=asset.id)
    assert final is not None and final.lifecycle is AssetLifecycle.DELETED
