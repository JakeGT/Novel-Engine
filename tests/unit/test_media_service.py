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


_PNG = b"\x89PNG\r\n\x1a\n"


async def _scene_and_beat(mutation) -> None:
    from backend.domain.operations import CreatePlanItemOp, RetirePlanItemOp

    draft = await mutation.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[
            CreatePlanItemOp(id="pln_scene", kind="scene", title="Low tide"),
            CreatePlanItemOp(id="pln_beat", kind="beat", title="The turn"),
            CreatePlanItemOp(id="pln_gone", kind="scene", title="Cut"),
            CreateEntityOp(id="ent_1", type="Location", name="Harbor"),
        ],
        expected_head=0,
    )
    await mutation.commit(_scope(), draft.id)
    gone = await mutation.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[RetirePlanItemOp(id="pln_gone")],
        expected_head=1,
    )
    await mutation.commit(_scope(), gone.id)


@pytest.mark.asyncio
async def test_a_picture_is_shown_on_a_scene_and_only_on_a_scene(monkeypatch) -> None:
    # The bytes are checked elsewhere; here only where the picture goes matters.
    monkeypatch.setattr(
        "backend.services.media_service.strip_metadata", lambda data, content_type: data
    )
    service, _media, _objects, _canon, mutation = _services()
    scope = _scope()
    await _scene_and_beat(mutation)

    result, link, version = await service.add_scene_picture(
        scope, WS, PR, scene_id="pln_scene", data=_PNG, name=None, expected_revision=2
    )
    assert result.revision == 3
    assert link.role is LinkRole.DEPICTS
    assert (link.target_record_type, link.target_record_id) == ("plan_item", "pln_scene")
    assert link.asset_version_id == version.id
    asset = await service.get_asset(scope, WS, PR, link.asset_id)
    assert asset.name == "Picture for Low tide"

    # Not a beat, not a scene taken out of the plan, not a scene that does not exist.
    for target in ("pln_beat", "pln_gone", "pln_missing"):
        with pytest.raises(NotFoundError):
            await service.add_scene_picture(
                scope, WS, PR, scene_id=target, data=_PNG, name=None, expected_revision=3
            )
    # The same holds for a link made by hand: the commit refuses it.
    for target in ("pln_beat", "pln_gone", "pln_missing"):
        with pytest.raises(CanonValidationError):
            await service.create_link(
                scope,
                WS,
                PR,
                asset_id=link.asset_id,
                asset_version_id=version.id,
                target_id=target,
                target_record_type="plan_item",
                role=LinkRole.DEPICTS.value,
                expected_revision=3,
            )


@pytest.mark.asyncio
async def test_the_library_lists_every_picture_including_those_shown_nowhere(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.services.media_service.strip_metadata", lambda data, content_type: data
    )
    service, _media, objects, _canon, mutation = _services()
    scope = _scope()
    await _scene_and_beat(mutation)

    _, on_entry, _ = await service.add_picture(
        scope, WS, PR, entity_id="ent_1", data=_PNG, name="Harbor at dawn", expected_revision=2
    )
    _, on_scene, _ = await service.add_scene_picture(
        scope, WS, PR, scene_id="pln_scene", data=_PNG, name="Low water", expected_revision=3
    )
    # Removed from the entry: the picture stays in the library, shown nowhere.
    await service.retire_link(scope, WS, PR, on_entry.id, expected_revision=4)
    # Choosing it for the scene links it again; nothing is copied.
    await service.create_link(
        scope,
        WS,
        PR,
        asset_id=on_entry.asset_id,
        asset_version_id=on_entry.asset_version_id,
        target_id="pln_scene",
        target_record_type="plan_item",
        role=LinkRole.DEPICTS.value,
        expected_revision=5,
    )
    # Something that is not a picture is not offered.
    other = await service.register_asset(scope, WS, PR, name="Notes")
    stat = objects.put(b"text", workspace_id=WS, project_id=PR, content_type="text/plain")
    await service.register_version(scope, WS, PR, other.id, object_key=stat.key)

    library = await service.picture_library(scope, WS, PR)
    by_name = {picture.asset.name: picture for picture in library}
    assert set(by_name) == {"Harbor at dawn", "Low water"}
    assert by_name["Harbor at dawn"].shown == 1
    assert by_name["Low water"].version.id == on_scene.asset_version_id
    assert len(await service.list_links(scope, WS, PR, target_id="pln_scene")) == 2

    await service.retire_link(scope, WS, PR, on_scene.id, expected_revision=6)
    library = await service.picture_library(scope, WS, PR)
    assert {picture.asset.name: picture.shown for picture in library}["Low water"] == 0


@pytest.mark.asyncio
async def test_a_picture_added_only_in_a_what_if_stays_out_of_the_main_library(
    monkeypatch,
) -> None:
    from backend.domain.branching import working_in

    monkeypatch.setattr(
        "backend.services.media_service.strip_metadata", lambda data, content_type: data
    )
    service, _media, _objects, canon, mutation = _services()
    scope = _scope()
    await _scene_and_beat(mutation)
    await service.add_picture(
        scope, WS, PR, entity_id="ent_1", data=_PNG, name="Harbor at dawn", expected_revision=2
    )
    root = await canon._journal.ensure_root_branch(workspace_id=WS, project_id=PR)
    await canon.fork_branch(
        workspace_id=WS,
        project_id=PR,
        parent_branch_id=root.id,
        branch_id="branch_whatif",
        name="What if",
        description=None,
        actor_user_id="usr_1",
    )
    with working_in("branch_whatif"):
        await service.add_picture(
            scope, WS, PR, entity_id="ent_1", data=_PNG, name="Storm", expected_revision=3
        )
        in_what_if = {p.asset.name for p in await service.picture_library(scope, WS, PR)}
    in_main = {p.asset.name for p in await service.picture_library(scope, WS, PR)}

    assert in_what_if == {"Harbor at dawn", "Storm"}
    assert in_main == {"Harbor at dawn"}


@pytest.mark.asyncio
async def test_a_picture_whose_link_is_refused_leaves_nothing_behind(monkeypatch) -> None:
    from backend.domain.journal import HeadConflict

    monkeypatch.setattr(
        "backend.services.media_service.strip_metadata", lambda data, content_type: data
    )
    service, media, objects, _canon, mutation = _services()
    scope = _scope()
    await _scene_and_beat(mutation)

    # The story moved on since the author loaded it: the link is refused.
    with pytest.raises(HeadConflict):
        await service.add_picture(
            scope, WS, PR, entity_id="ent_1", data=_PNG, name="Stale", expected_revision=1
        )

    assert media.assets == {}
    assert media.versions == {}
    assert media.events == []
    assert objects.list_objects(workspace_id=WS, project_id=PR) == []
    assert await service.picture_library(scope, WS, PR) == []


@pytest.mark.asyncio
async def test_a_picture_on_a_scene_taken_out_of_the_plan_is_not_counted_as_shown(
    monkeypatch,
) -> None:
    from backend.domain.operations import RetirePlanItemOp

    monkeypatch.setattr(
        "backend.services.media_service.strip_metadata", lambda data, content_type: data
    )
    service, _media, _objects, _canon, mutation = _services()
    scope = _scope()
    await _scene_and_beat(mutation)
    await service.add_scene_picture(
        scope, WS, PR, scene_id="pln_scene", data=_PNG, name="Low water", expected_revision=2
    )
    assert [p.shown for p in await service.picture_library(scope, WS, PR)] == [1]

    draft = await mutation.create_draft(
        scope,
        workspace_id=WS,
        project_id=PR,
        operations=[RetirePlanItemOp(id="pln_scene")],
        expected_head=3,
    )
    await mutation.commit(scope, draft.id)

    library = await service.picture_library(scope, WS, PR)
    # Still offered, so it can be used again, but shown nowhere now.
    assert [(p.asset.name, p.shown) for p in library] == [("Low water", 0)]
