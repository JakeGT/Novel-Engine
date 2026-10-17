import pytest

from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.media import LinkRole
from backend.domain.operations import CreateEntityOp
from backend.domain.validation import CanonValidationError
from backend.services.media_service import MediaService
from backend.services.mutation_service import MutationService
from tests.doubles import (
    InMemoryCanonJournal,
    InMemoryCanonRepository,
    InMemoryMediaStore,
    InMemoryObjectStore,
)

pytestmark = pytest.mark.application

WS, PR = "ws_media", "pr_media"


def _services():
    media = InMemoryMediaStore()
    objects = InMemoryObjectStore()
    journal = InMemoryCanonJournal()
    canon = InMemoryCanonRepository(journal=journal, media=media)
    mutation = MutationService(journal, canon, local_mode=True)
    service = MediaService(
        media=media, objects=objects, mutation=mutation, reader=canon, local_mode=True
    )
    return service, media, objects, canon, mutation


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="usr_1", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
    )


@pytest.mark.asyncio
async def test_register_asset_version_and_link_lifecycle() -> None:
    service, _media, objects, _canon, mutation = _services()
    scope = _scope()

    asset = await service.register_asset(scope, WS, PR, name="Harbor map")
    stat = objects.put(b"PNG", workspace_id=WS, project_id=PR, content_type="image/png")
    version = await service.register_version(
        scope, WS, PR, asset.id, object_key=stat.key, attribution="Seed archive"
    )

    assert version.checksum == stat.checksum
    assert version.byte_length == len(b"PNG")
    assert version.content_type == "image/png"
    assert version.attribution == "Seed archive"

    draft = await mutation.create_draft(
        scope,
        workspace_id=WS,
        project_id=PR,
        operations=[CreateEntityOp(id="ent_1", type="Location", name="Harbor")],
        expected_head=0,
    )
    await mutation.commit(scope, draft.id)

    result, link = await service.create_link(
        scope,
        WS,
        PR,
        asset_id=asset.id,
        asset_version_id=version.id,
        target_id="ent_1",
        role=LinkRole.EVIDENCE_FOR.value,
        expected_revision=1,
    )
    assert link.role is LinkRole.EVIDENCE_FOR
    assert link.target_record_id == "ent_1"
    assert result.revision == 2

    assert len(await service.list_links(scope, WS, PR, asset_id=asset.id)) == 1

    await service.retire_link(scope, WS, PR, link.id, expected_revision=2)
    assert await service.list_links(scope, WS, PR, asset_id=asset.id) == []
    retired = await service.list_links(scope, WS, PR, asset_id=asset.id, include_retired=True)
    assert len(retired) == 1


@pytest.mark.asyncio
async def test_register_version_requires_present_object() -> None:
    service, _media, _objects, _canon, _mutation = _services()
    scope = _scope()
    asset = await service.register_asset(scope, WS, PR, name="Audio")
    with pytest.raises(ValidationError):
        await service.register_version(scope, WS, PR, asset.id, object_key="missing")


@pytest.mark.asyncio
async def test_unknown_role_and_cross_project_asset_rejected() -> None:
    service, _media, _objects, _canon, _mutation = _services()
    scope = _scope()
    other = AccessScope(
        actor_user_id="usr_2", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id="pr_other"
    )
    asset = await service.register_asset(scope, WS, PR, name="Map")

    with pytest.raises(ValidationError):
        await service.create_link(
            scope,
            WS,
            PR,
            asset_id=asset.id,
            target_id="ent_1",
            role="NOT_A_ROLE",
            expected_revision=0,
        )

    # Asset registered in PR is not visible from a sibling project.
    with pytest.raises(NotFoundError):
        await service.get_asset(other, WS, "pr_other", asset.id)


@pytest.mark.asyncio
async def test_link_to_unknown_target_is_rejected_in_tx() -> None:
    service, _media, objects, _canon, _mutation = _services()
    scope = _scope()
    asset = await service.register_asset(scope, WS, PR, name="Map")
    stat = objects.put(b"PNG", workspace_id=WS, project_id=PR, content_type="image/png")
    await service.register_version(scope, WS, PR, asset.id, object_key=stat.key)

    with pytest.raises(CanonValidationError):
        await service.create_link(
            scope, WS, PR, asset_id=asset.id, target_id="ent_missing", expected_revision=0
        )


@pytest.mark.asyncio
async def test_a_picture_is_cleaned_off_the_event_loop(monkeypatch) -> None:
    import asyncio

    from backend.domain.image_metadata import strip_metadata

    service, *_ = _services()
    ran: list[object] = []
    real = asyncio.to_thread

    async def spy(func, /, *args, **kwargs):
        ran.append(func)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    # A JPEG with no frame and no image data is refused once its structure is walked.
    with pytest.raises(ValidationError):
        await service.add_picture(
            _scope(),
            WS,
            PR,
            entity_id="ent_1",
            data=b"\xff\xd8\xff\xd9",
            name=None,
            expected_revision=0,
        )
    assert strip_metadata in ran
