"""Media identity, lifecycle, and evidence links against PostgreSQL (2I)."""

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEntityOp
from backend.domain.validation import CanonValidationError
from backend.services.media_service import MediaService
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryObjectStore

pytestmark = pytest.mark.integration

WS, PR = "ws_media_it", "pr_media_it"


def _scope(project_id: str = PR) -> AccessScope:
    return AccessScope(
        actor_user_id="usr_media", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=project_id
    )


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    repository = PostgresCanonRepository(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    objects = InMemoryObjectStore()
    media = MediaService(
        media=PostgresMediaStore(factory),
        objects=objects,
        mutation=mutation,
        reader=repository,
        journal=journal,
        local_mode=True,
    )
    try:
        yield media, mutation, repository, objects
    finally:
        await engine.dispose()


async def _entity(mutation: MutationService, entity_id: str, project_id: str = PR) -> None:
    draft = await mutation.create_draft(
        _scope(project_id),
        workspace_id=WS,
        project_id=project_id,
        operations=[CreateEntityOp(id=entity_id, type="Character", name="Mara")],
        expected_head=0,
    )
    await mutation.commit(_scope(project_id), draft.id)


async def _version(media: MediaService, objects, project_id: str = PR):
    stat = objects.put(
        b"portrait-bytes", workspace_id=WS, project_id=project_id, content_type="image/png"
    )
    asset = await media.register_asset(_scope(project_id), WS, project_id, name="Portrait")
    version = await media.register_version(
        _scope(project_id),
        WS,
        project_id,
        asset.id,
        object_key=stat.key,
        attribution="Studio",
        licence="CC-BY-4.0",
    )
    return asset, version


@pytest.mark.asyncio
async def test_link_and_retire_link_are_revisioned_canon(stack) -> None:
    media, mutation, repository, objects = stack
    await _entity(mutation, "ent_media_1")
    asset, version = await _version(media, objects)
    assert version.checksum and version.byte_length == len(b"portrait-bytes")

    result, link = await media.create_link(
        _scope(),
        WS,
        PR,
        asset_id=asset.id,
        asset_version_id=version.id,
        target_id="ent_media_1",
        role="DEPICTS",
        expected_revision=1,
    )
    assert result.revision == 2
    assert link.created_revision == 2 and link.is_active

    history = await repository.list_record_versions(
        workspace_id=WS, project_id=PR, record_type="asset_link", record_id=link.id
    )
    assert [entry.operation_type for entry in history] == ["link_asset"]

    retired = await media.retire_link(_scope(), WS, PR, link.id, expected_revision=2)
    assert retired.revision == 3
    stored = await repository.get_link(workspace_id=WS, project_id=PR, link_id=link.id)
    assert stored is not None and stored.retired_revision == 3
    assert await media.list_links(_scope(), WS, PR) == []
    assert len(await media.list_links(_scope(), WS, PR, include_retired=True)) == 1


@pytest.mark.asyncio
async def test_retired_or_foreign_assets_cannot_receive_links(stack) -> None:
    media, mutation, _repository, objects = stack
    await _entity(mutation, "ent_media_2")
    asset, version = await _version(media, objects)

    await media.retire_version(_scope(), WS, PR, version.id)
    with pytest.raises(CanonValidationError, match="inactive_asset_version"):
        await media.create_link(
            _scope(),
            WS,
            PR,
            asset_id=asset.id,
            asset_version_id=version.id,
            target_id="ent_media_2",
            expected_revision=1,
        )

    await media.retire_asset(_scope(), WS, PR, asset.id)
    with pytest.raises(CanonValidationError, match="inactive_asset"):
        await media.create_link(
            _scope(), WS, PR, asset_id=asset.id, target_id="ent_media_2", expected_revision=1
        )

    # An asset registered in another project is invisible to this project's commit.
    other_asset, _ = await _version(media, objects, project_id="pr_media_other")
    with pytest.raises(CanonValidationError, match="unknown_asset"):
        await media.create_link(
            _scope(), WS, PR, asset_id=other_asset.id, target_id="ent_media_2", expected_revision=1
        )
