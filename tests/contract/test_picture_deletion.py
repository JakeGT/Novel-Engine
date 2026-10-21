"""Deleting one picture's files safely (AST-008), and what an upload says it received
(AST-002): only where the picture itself is shown counts, the picture is marked deleted
before any file goes, a file another picture uses stays, preparation finishing after a
deletion keeps nothing, and an archive leaves out what was said about a deleted picture."""

from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest

import backend.services.media_service as media_module
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.media import AssetLifecycle, MediaLifecycleAction
from backend.domain.media_observations import MediaObservation, ObservationKind, ObservationState
from backend.services.canon_archive import ArchiveError
from backend.services.media_service import PictureTooSlow, receive_picture
from tests.contract.test_media_processing import _png, _run
from tests.contract.test_picture_library import _add, _head, _story

pytestmark = pytest.mark.contract


def _ids(container) -> tuple[str, str]:
    return container.settings.AUTH_LOCAL_WORKSPACE_ID, container.settings.AUTH_LOCAL_PROJECT_ID


def _owner(container) -> AccessScope:
    workspace, project = _ids(container)
    return AccessScope(
        actor_user_id="usr_owner",
        auth_mode=AuthMode.LOCAL,
        workspace_id=workspace,
        project_id=project,
    )


def _take_off(client, base: str, link_id: str) -> None:
    off = client.post(
        f"{base}/links/{link_id}/retire", json={"expected_revision": _head(client, base)}
    )
    assert off.status_code == 200, off.text


def _asset(container, asset_id: str):
    workspace, project = _ids(container)
    return asyncio.run(
        container.media_service._media.get_asset(
            workspace_id=workspace, project_id=project, asset_id=asset_id
        )
    )


def test_an_older_picture_still_shown_is_not_deleted_behind_newer_links(
    auth_client, fake_container, monkeypatch
):
    # Only the newest links were looked at, of any picture: an older picture still shown
    # fell outside them and counted as shown nowhere.
    monkeypatch.setattr(media_module, "MAX_LIBRARY_LINKS", 1)
    base, mara, _ = _story(auth_client, fake_container)
    older = _add(auth_client, base, mara, "Older portrait", "teal").json()
    _add(auth_client, base, mara, "Newer portrait", "navy")
    refused = auth_client.post(f"{base}/assets/{older['link']['asset_id']}/delete")
    assert refused.status_code == 409, refused.text
    assert "one entry or scene" in refused.json()["detail"]


def test_a_picture_only_on_a_removed_scene_can_be_deleted(auth_client, fake_container):
    base, _, scene = _story(auth_client, fake_container)
    made = _add(auth_client, base, scene, "Harbor at dusk", "navy", scene=True).json()
    removed = auth_client.post(
        f"{base}/plan/{scene}/remove", json={"expected_revision": _head(auth_client, base)}
    )
    assert removed.status_code == 200, removed.text
    asset_id = made["link"]["asset_id"]
    library = auth_client.get(f"{base}/pictures").json()["items"]
    assert [item["shown"] for item in library if item["asset_id"] == asset_id] == [0]
    deleted = auth_client.post(f"{base}/assets/{asset_id}/delete")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["files_removed"] == 1


def test_a_picture_is_marked_deleted_before_its_files_go(auth_client, fake_container, monkeypatch):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    _run(fake_container)
    _take_off(auth_client, base, made["link"]["id"])
    asset_id = made["link"]["asset_id"]
    media = fake_container.media_service
    seen: list[AssetLifecycle] = []
    real_delete = media._objects.delete

    workspace, project = _ids(fake_container)

    def delete(key, **scope):
        seen.append(media._media.assets[(workspace, project, asset_id)].lifecycle)
        return real_delete(key, **scope)

    monkeypatch.setattr(media._objects, "delete", delete)
    assert auth_client.post(f"{base}/assets/{asset_id}/delete").status_code == 200
    assert seen and set(seen) == {AssetLifecycle.DELETED}


def test_a_picture_shown_again_while_deleting_is_put_back_untouched(
    auth_client, fake_container, monkeypatch
):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    _take_off(auth_client, base, made["link"]["id"])
    asset_id, version = made["link"]["asset_id"], made["version"]
    media = fake_container.media_service
    answers = iter([0, 1])

    async def shown(_w, _p, _a):
        return next(answers)

    monkeypatch.setattr(media, "_shown_anywhere", shown)
    refused = auth_client.post(f"{base}/assets/{asset_id}/delete")
    assert refused.status_code == 409, refused.text
    assert _asset(fake_container, asset_id).lifecycle is AssetLifecycle.ACTIVE
    workspace, project = _ids(fake_container)
    assert media._objects.stat(version["object_key"], workspace_id=workspace, project_id=project)
    actions = [
        event.action
        for event in asyncio.run(
            media._media.list_lifecycle_events(
                workspace_id=workspace, project_id=project, asset_id=asset_id
            )
        )
    ]
    assert MediaLifecycleAction.ASSET_DELETED not in actions
    content = auth_client.get(f"{base}/assets/{asset_id}/versions/{version['id']}/content")
    assert content.status_code == 200


def test_a_file_two_pictures_share_is_kept_and_counted_once(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    first, key = made["link"]["asset_id"], made["version"]["object_key"]
    used = auth_client.get(f"{base}/pictures").json()["storage"]["used_bytes"]
    other = auth_client.post(f"{base}/assets", json={"name": "Same file"}).json()["id"]
    shared = auth_client.post(f"{base}/assets/{other}/versions", json={"object_key": key})
    assert shared.status_code == 201, shared.text
    assert auth_client.get(f"{base}/pictures").json()["storage"]["used_bytes"] == used

    _take_off(auth_client, base, made["link"]["id"])
    deleted = auth_client.post(f"{base}/assets/{first}/delete").json()
    assert deleted["files_removed"] == 0 and deleted["bytes_freed"] == 0
    workspace, project = _ids(fake_container)
    objects = fake_container.media_service._objects
    assert objects.stat(key, workspace_id=workspace, project_id=project) is not None
    opened = auth_client.get(f"{base}/assets/{other}/versions/{shared.json()['id']}/content")
    assert opened.status_code == 200
    assert auth_client.get(f"{base}/pictures").json()["storage"]["used_bytes"] == used

    # The last picture using it takes the file with it.
    last = auth_client.post(f"{base}/assets/{other}/delete").json()
    assert last["files_removed"] == 1
    assert objects.stat(key, workspace_id=workspace, project_id=project) is None


def _objects_of(container) -> set[str]:
    workspace, project = _ids(container)
    return {
        stat.key
        for stat in container.media_service._objects.list_objects(
            workspace_id=workspace, project_id=project
        )
    }


def test_copies_finished_after_a_deletion_are_not_kept(auth_client, fake_container, monkeypatch):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    _take_off(auth_client, base, made["link"]["id"])
    asset_id, version_id = made["link"]["asset_id"], made["version"]["id"]
    workspace, project = _ids(fake_container)
    media = fake_container.media_service
    real = media._processor

    class _DeletedWhileWorking:
        version = real.version

        def capability(self):
            return real.capability()

        async def process(self, data):
            done = await real.process(data)
            await media.delete_picture(_owner(fake_container), workspace, project, asset_id)
            return done

    monkeypatch.setattr(media, "_processor", _DeletedWhileWorking())
    outcomes = asyncio.run(media.process_version(workspace, project, version_id))
    assert outcomes["outcome"] == "gone"
    assert _objects_of(fake_container) == set()
    variants = asyncio.run(
        media._processing.list_variants(
            workspace_id=workspace, project_id=project, version_ids=[version_id]
        )
    )
    assert variants == {}


def test_copies_recorded_just_as_the_picture_was_deleted_are_removed(
    auth_client, fake_container, monkeypatch
):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    asset_id, version = made["link"]["asset_id"], made["version"]
    workspace, project = _ids(fake_container)
    media = fake_container.media_service
    real_complete = media._processing.complete

    async def complete(**kwargs):
        # The deletion lands between the last look and the copies being recorded.
        await media._media.delete_original(
            workspace_id=workspace, project_id=project, version_id=version["id"]
        )
        await media._media.delete_asset(
            workspace_id=workspace, project_id=project, asset_id=asset_id
        )
        return await real_complete(**kwargs)

    monkeypatch.setattr(media._processing, "complete", complete)
    outcome = asyncio.run(media.process_version(workspace, project, version["id"]))
    assert outcome["outcome"] == "gone"
    assert _objects_of(fake_container) == {version["object_key"]}
    assert (
        asyncio.run(
            media._processing.list_variants(
                workspace_id=workspace, project_id=project, version_ids=[version["id"]]
            )
        )
        == {}
    )


def test_an_archive_leaves_out_what_was_said_about_a_deleted_picture(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    gone = _add(auth_client, base, mara, "Portrait", "teal").json()
    kept = _add(auth_client, base, mara, "Sketch", "navy").json()
    workspace, project = _ids(fake_container)
    asyncio.run(
        fake_container.observation_store.add(
            [
                MediaObservation(
                    workspace_id=workspace,
                    project_id=project,
                    id=f"obs_{index}",
                    asset_id=made["link"]["asset_id"],
                    asset_version_id=made["version"]["id"],
                    kind=ObservationKind.CAPTION,
                    text=text,
                    state=ObservationState.ACCEPTED,
                )
                for index, (made, text) in enumerate(
                    [(gone, "A woman in a green coat"), (kept, "A boat")]
                )
            ]
        )
    )
    _take_off(auth_client, base, gone["link"]["id"])
    assert auth_client.post(f"{base}/assets/{gone['link']['asset_id']}/delete").status_code == 200
    archive = asyncio.run(fake_container.archive_service.export_project(workspace, project))
    notes = archive["picture_notes"]["observations"]
    assert [note["asset_id"] for note in notes] == [kept["link"]["asset_id"]]


def test_only_a_new_story_restored_from_an_older_archive_has_the_picture_back(
    auth_client, fake_container
):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    _take_off(auth_client, base, made["link"]["id"])
    asset_id, version_id = made["link"]["asset_id"], made["version"]["id"]
    workspace, project = _ids(fake_container)
    before = asyncio.run(fake_container.archive_service.export_project(workspace, project))
    assert auth_client.post(f"{base}/assets/{asset_id}/delete").status_code == 200
    service = fake_container.archive_service

    # Never back into the story it came from: that story already has its own history.
    with pytest.raises(ArchiveError):
        asyncio.run(service.import_project(workspace, project, before))
    assert _asset(fake_container, asset_id).lifecycle is AssetLifecycle.DELETED

    # Into a new story, an archive saved before the deletion brings the picture back there.
    media = fake_container.media_service
    asyncio.run(service.import_project(workspace, "pr_from_before", before))
    restored = asyncio.run(
        media._media.get_asset(
            workspace_id=workspace, project_id="pr_from_before", asset_id=asset_id
        )
    )
    assert restored is not None and restored.lifecycle is AssetLifecycle.ACTIVE
    version = asyncio.run(
        media._media.get_version(
            workspace_id=workspace, project_id="pr_from_before", version_id=version_id
        )
    )
    assert version is not None
    assert media._objects.stat(
        version.object_key, workspace_id=workspace, project_id="pr_from_before"
    )


def test_a_restore_never_points_a_picture_at_a_smaller_copy(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    workspace, project = _ids(fake_container)
    archive = asyncio.run(fake_container.archive_service.export_project(workspace, project))
    target = "pr_restored_copy"
    media = fake_container.media_service
    # The target already holds a smaller copy with exactly the archived picture's bytes.
    original = media._objects.open(
        made["version"]["object_key"], workspace_id=workspace, project_id=project
    )
    copy = media._objects.put(original, workspace_id=workspace, project_id=target)
    from backend.domain.media import MediaVariant, VariantKind

    media._processing.variants[(workspace, target, "asset_version_other")] = {
        "thumbnail": MediaVariant(
            workspace_id=workspace,
            project_id=target,
            asset_version_id="asset_version_other",
            kind=VariantKind.THUMBNAIL,
            object_key=copy.key,
            content_type="image/webp",
            width=1,
            height=1,
            byte_length=copy.byte_length,
            checksum=copy.checksum,
            source_checksum="other",
            processor_version="test",
        )
    }
    asyncio.run(fake_container.archive_service.import_project(workspace, target, archive))
    restored = asyncio.run(
        media._media.get_version(
            workspace_id=workspace, project_id=target, version_id=made["version"]["id"]
        )
    )
    assert restored is not None and restored.object_key != copy.key


def test_a_digest_by_a_method_not_checked_is_said_so(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    made = auth_client.post(
        f"{base}/entities/{mara}/pictures",
        params={"expected_revision": _head(auth_client, base)},
        content=_png("navy"),
        headers={"Content-Digest": "sha-512=:AAAA:"},
    )
    assert made.status_code == 201, made.text
    received = made.json()["received"]
    assert received["digest"] == "not_checked" and "sha-256" in received["note"]
    plain = auth_client.post(
        f"{base}/entities/{mara}/pictures",
        params={"expected_revision": _head(auth_client, base)},
        content=_png("olive"),
    ).json()["received"]
    assert plain["digest"] == "none" and "note" not in plain
    picture = _png("teal")
    digest = base64.b64encode(hashlib.sha256(picture).digest()).decode()
    checked = auth_client.post(
        f"{base}/entities/{mara}/pictures",
        params={"expected_revision": _head(auth_client, base)},
        content=picture,
        headers={"Content-Digest": f"sha-256=:{digest}:"},
    ).json()["received"]
    assert checked["digest"] == "matched"


def test_an_upload_that_trickles_in_is_stopped_at_its_deadline() -> None:
    async def trickle():
        yield b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        while True:
            await asyncio.sleep(0.05)
            yield b"\x00"

    with pytest.raises(PictureTooSlow, match="took too long"):
        asyncio.run(receive_picture(trickle(), deadline_seconds=0.2))


def _restored(container, project_id: str, asset_id: str, version_id: str):
    """The picture, its version, its links and the main story's history in one story."""
    workspace, _ = _ids(container)
    media = container.media_service._media
    service = container.archive_service

    async def read():
        branch = await service._root_branch_id(workspace, project_id)
        commits = await service._canon.list_commits(
            workspace_id=workspace, project_id=project_id, branch_id=branch, limit=1000
        )
        links = await container.media_service._reader.list_links(
            workspace_id=workspace, project_id=project_id, asset_id=asset_id, include_retired=True
        )
        return (
            await media.get_asset(workspace_id=workspace, project_id=project_id, asset_id=asset_id),
            await media.get_version(
                workspace_id=workspace, project_id=project_id, version_id=version_id
            ),
            sorted((link.id, link.created_revision, link.retired_revision) for link in links),
            [commit.revision for commit in commits],
        )

    return asyncio.run(read())


def test_a_story_with_a_retired_picture_once_shown_is_restored_whole(auth_client, fake_container):
    # Replaying the change that first showed the picture was checked against the
    # picture as it is now (retired) and refused, so the story could not be restored.
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    asset_id, version_id = made["link"]["asset_id"], made["version"]["id"]
    retired = auth_client.post(f"{base}/assets/{asset_id}/retire")
    assert retired.status_code == 200, retired.text
    workspace, project = _ids(fake_container)
    archive = asyncio.run(fake_container.archive_service.export_project(workspace, project))
    asyncio.run(fake_container.archive_service.import_project(workspace, "pr_retired", archive))

    source = _restored(fake_container, project, asset_id, version_id)
    target = _restored(fake_container, "pr_retired", asset_id, version_id)
    assert target[0] is not None and target[0].lifecycle is AssetLifecycle.RETIRED
    assert target[0].retired_revision == source[0].retired_revision
    assert target[1] == source[1].model_copy(
        update={"project_id": "pr_retired", "object_key": target[1].object_key}
    )
    assert target[2] == source[2] and target[2]
    assert target[3] == source[3]
    # Nothing restored brings it back: it is still not prepared or opened.
    opened = asyncio.run(
        fake_container.media_service.process_version(workspace, "pr_retired", version_id)
    )
    assert opened["outcome"] == "gone"


def test_a_story_with_a_deleted_picture_once_shown_is_restored_with_its_tombstone(
    auth_client, fake_container
):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    asset_id, version_id = made["link"]["asset_id"], made["version"]["id"]
    _take_off(auth_client, base, made["link"]["id"])
    assert auth_client.post(f"{base}/assets/{asset_id}/delete").status_code == 200
    workspace, project = _ids(fake_container)
    archive = asyncio.run(fake_container.archive_service.export_project(workspace, project))
    asyncio.run(fake_container.archive_service.import_project(workspace, "pr_deleted", archive))

    source = _restored(fake_container, project, asset_id, version_id)
    target = _restored(fake_container, "pr_deleted", asset_id, version_id)
    assert target[0] is not None and target[0].lifecycle is AssetLifecycle.DELETED
    assert target[0].retired_revision == source[0].retired_revision
    version = target[1]
    assert version is not None and version.deleted_at == source[1].deleted_at
    assert version.retired_revision == source[1].retired_revision
    assert (version.checksum, version.byte_length) == (source[1].checksum, source[1].byte_length)
    # The file stays gone: no file is restored for a deleted original.
    assert _objects_of_project(fake_container, "pr_deleted") == set()
    assert target[2] == source[2] and all(link[2] is not None for link in target[2])
    assert target[3] == source[3]


def _objects_of_project(container, project_id: str) -> set[str]:
    workspace, _ = _ids(container)
    return {
        stat.key
        for stat in container.media_service._objects.list_objects(
            workspace_id=workspace, project_id=project_id
        )
    }
