"""Pictures for a stretch of story time and one chosen version (AST-001), the two-step
upload (AST-002), and what a model said about a deleted picture (AST-008)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import timedelta

import pytest

from backend.domain.identity import utcnow
from backend.domain.media import MediaLifecycleAction, UploadState
from backend.domain.media_observations import MediaObservation, ObservationKind, ObservationState
from tests.contract.test_context import _context
from tests.contract.test_media_processing import _png
from tests.contract.test_picture_context import _item, _scene_with_picture
from tests.contract.test_picture_deletion import _ids, _take_off
from tests.contract.test_picture_library import _add, _head, _story
from tests.contract.test_picture_observations import _view

pytestmark = pytest.mark.contract


# ------------------------------------------------------------------- AST-002


def _start(client, base: str, target: str, data: bytes, kind: str = "entity", **extra):
    return client.post(
        f"{base}/uploads",
        json={
            "for": kind,
            "id": target,
            "byte_length": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            **extra,
        },
    )


def _two_step(client, base: str, target: str, data: bytes, kind: str = "entity") -> dict:
    started = _start(client, base, target, data, kind, name="Mara at the window")
    assert started.status_code == 201, started.text
    upload = started.json()
    assert upload["state"] == "waiting" and upload["for"] == kind
    assert upload["send_to"] == f"{base}/uploads/{upload['id']}"
    sent = client.put(upload["send_to"], content=data)
    assert sent.status_code == 200, sent.text
    assert sent.json()["upload"]["state"] == "received"
    params = {} if kind == "picture" else {"expected_revision": _head(client, base)}
    finished = client.post(f"{base}/uploads/{upload['id']}/finish", params=params)
    assert finished.status_code == 200, finished.text
    return finished.json()


def test_a_picture_sent_in_two_steps_is_checked_then_shown(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    data = _png("teal")
    made = _two_step(auth_client, base, mara, data)
    assert made["upload"]["state"] == "finished"
    assert made["link"]["target_record_id"] == mara
    library = auth_client.get(f"{base}/pictures").json()["items"]
    assert [item["name"] for item in library] == ["Mara at the window"]
    # Finishing again answers with what it made, and makes nothing more.
    again = auth_client.post(
        f"{base}/uploads/{made['upload']['id']}/finish",
        params={"expected_revision": _head(auth_client, base)},
    )
    assert again.status_code == 200 and again.json()["version"]["id"] == made["version"]["id"]
    assert len(auth_client.get(f"{base}/pictures").json()["items"]) == 1


def test_what_arrives_must_be_what_was_promised(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    data = _png("teal")
    upload = _start(auth_client, base, mara, data).json()
    other = _png("navy")
    wrong = auth_client.put(f"{base}/uploads/{upload['id']}", content=other[: len(data)])
    assert wrong.status_code in (400, 413, 422), wrong.text
    # Nothing was kept: it can be sent again, rightly this time.
    assert auth_client.get(f"{base}/uploads/{upload['id']}").json()["state"] == "waiting"
    digest = base64.b64encode(hashlib.sha256(b"something else").digest()).decode()
    refused = auth_client.put(
        f"{base}/uploads/{upload['id']}",
        content=data,
        headers={"Content-Digest": f"sha-256=:{digest}:"},
    )
    assert refused.status_code == 400, refused.text
    sent = auth_client.put(f"{base}/uploads/{upload['id']}", content=data)
    assert sent.status_code == 200, sent.text
    # Sending twice is refused once it has arrived.
    twice = auth_client.put(f"{base}/uploads/{upload['id']}", content=data)
    assert twice.status_code == 409


def test_a_two_step_upload_is_refused_before_anything_is_sent(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    from backend.services.media_service import MAX_PICTURE_BYTES

    big = auth_client.post(
        f"{base}/uploads",
        json={
            "for": "entity",
            "id": mara,
            "byte_length": MAX_PICTURE_BYTES + 1,
            "sha256": "a" * 64,
        },
    )
    assert big.status_code == 413, big.text
    nowhere = _start(auth_client, base, "ent_nobody", _png())
    assert nowhere.status_code == 404
    bad = auth_client.post(
        f"{base}/uploads",
        json={"for": "entity", "id": mara, "byte_length": 10, "sha256": "Z" * 64},
    )
    assert bad.status_code in (400, 422)


def test_an_unfinished_upload_is_cleared_after_its_hour(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    data = _png("teal")
    upload = _start(auth_client, base, mara, data).json()
    assert auth_client.put(f"{base}/uploads/{upload['id']}", content=data).status_code == 200
    workspace, project = _ids(fake_container)
    store = fake_container.media_service._media
    key = (workspace, project, upload["id"])
    staged = store.uploads[key].staged_key
    store.uploads[key] = store.uploads[key].model_copy(
        update={"expires_at": utcnow() - timedelta(minutes=1)}
    )
    finish = auth_client.post(
        f"{base}/uploads/{upload['id']}/finish",
        params={"expected_revision": _head(auth_client, base)},
    )
    assert finish.status_code == 409 and "within the hour" in finish.json()["detail"]
    assert asyncio.run(fake_container.media_service.expire_uploads()) == 1
    assert store.uploads[key].state is UploadState.EXPIRED
    objects = fake_container.media_service._objects
    assert objects.stat(staged, workspace_id=workspace, project_id=project) is None
    assert auth_client.get(f"{base}/pictures").json()["items"] == []


def test_an_upload_being_finished_is_not_cleared_from_under_it(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    data = _png("teal")
    upload = _start(auth_client, base, mara, data).json()
    assert auth_client.put(f"{base}/uploads/{upload['id']}", content=data).status_code == 200
    workspace, project = _ids(fake_container)
    store = fake_container.media_service._media
    objects = fake_container.media_service._objects
    key = (workspace, project, upload["id"])
    staged = store.uploads[key].staged_key
    # Finishing began just before its hour was up: the sweep leaves it and its file.
    store.uploads[key] = store.uploads[key].model_copy(
        update={"state": UploadState.FINISHING, "expires_at": utcnow() - timedelta(minutes=1)}
    )
    assert asyncio.run(fake_container.media_service.expire_uploads()) == 0
    assert store.uploads[key].state is UploadState.FINISHING
    assert objects.stat(staged, workspace_id=workspace, project_id=project) is not None
    # One stuck finishing long past its hour is cleared after all.
    store.uploads[key] = store.uploads[key].model_copy(
        update={"expires_at": utcnow() - timedelta(minutes=30)}
    )
    assert asyncio.run(fake_container.media_service.expire_uploads()) == 1
    assert objects.stat(staged, workspace_id=workspace, project_id=project) is None


def test_a_finish_that_fails_after_the_picture_is_in_never_makes_a_second(
    auth_client, fake_container, monkeypatch
):
    base, mara, _ = _story(auth_client, fake_container)
    data = _png("teal")
    upload = _start(auth_client, base, mara, data).json()
    assert auth_client.put(f"{base}/uploads/{upload['id']}", content=data).status_code == 200
    reader = fake_container.media_service._reader
    real_get_link = reader.get_link
    calls = {"n": 0}

    async def failing_once(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the store went away")
        return await real_get_link(**kwargs)

    monkeypatch.setattr(reader, "get_link", failing_once)
    with pytest.raises(RuntimeError):
        auth_client.post(
            f"{base}/uploads/{upload['id']}/finish",
            params={"expected_revision": _head(auth_client, base)},
        )
    assert calls["n"] == 1
    assert auth_client.get(f"{base}/uploads/{upload['id']}").json()["state"] == "finished"
    again = auth_client.post(
        f"{base}/uploads/{upload['id']}/finish",
        params={"expected_revision": _head(auth_client, base)},
    )
    assert again.status_code == 200, again.text
    assert again.json()["link"]["target_record_id"] == mara
    assert len(auth_client.get(f"{base}/pictures").json()["items"]) == 1


def _waiting(fake_container, actor: str | None, count: int) -> None:
    from backend.domain.media import PictureUpload, UploadTarget

    workspace, project = _ids(fake_container)
    store = fake_container.media_service._media
    now = utcnow()
    for index in range(count):
        upload = PictureUpload(
            workspace_id=workspace,
            project_id=project,
            id=f"upl_{actor}_{index}_{len(store.uploads)}",
            actor_user_id=actor,
            target_type=UploadTarget.ENTITY,
            target_id="ent_x",
            expected_bytes=10,
            expected_sha256="a" * 64,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        store.uploads[(workspace, project, upload.id)] = upload


def test_how_many_uploads_may_be_on_their_way_is_counted_per_person(auth_client, fake_container):
    from backend.services.media_service import MAX_OPEN_UPLOADS, MAX_OPEN_UPLOADS_PER_WORKSPACE

    base, mara, _ = _story(auth_client, fake_container)
    data = _png("teal")
    # Someone else's uploads do not use up this person's places.
    _waiting(fake_container, "usr_someone_else", MAX_OPEN_UPLOADS)
    first = _start(auth_client, base, mara, data)
    assert first.status_code == 201, first.text
    mine = first.json()
    store = fake_container.media_service._media
    me = next(u for u in store.uploads.values() if u.id == mine["id"]).actor_user_id
    _waiting(fake_container, me, MAX_OPEN_UPLOADS - 1)
    full = _start(auth_client, base, mara, data)
    assert full.status_code == 409 and "of your pictures" in full.json()["detail"]
    # The workspace as a whole still has a ceiling.
    for key, upload in list(store.uploads.items()):
        if upload.actor_user_id == me:
            del store.uploads[key]
    _waiting(fake_container, "usr_many", MAX_OPEN_UPLOADS_PER_WORKSPACE)
    ceiling = _start(auth_client, base, mara, data)
    assert ceiling.status_code == 409 and "in this workspace" in ceiling.json()["detail"]


def test_the_one_step_upload_still_works(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    assert _add(auth_client, base, mara, "Portrait", "teal").status_code == 201


# ------------------------------------------------------------------- AST-001


def _when(client, w, scene, at: int) -> None:
    got = client.put(
        f"{w['base']}/plan/{scene['id']}/when",
        json={"when": {"kind": "at", "at": at}, "expected_revision": _head(client, w["base"])},
    )
    assert got.status_code == 200, got.text


def test_a_picture_applies_from_and_until_a_story_time(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    view = _view(auth_client, w, asset_id)
    (link,) = view["links"]
    backwards = auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={
            "applies_from": {"kind": "at", "at": 10},
            "applies_until": {"kind": "at", "at": 5},
            "expected_revision": view["revision"],
        },
    )
    assert backwards.status_code in (400, 409, 422), backwards.text
    changed = auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={
            "applies_from": {"kind": "at", "at": 10},
            "applies_until": {"kind": "at", "at": 20},
            "expected_revision": view["revision"],
        },
    )
    assert changed.status_code == 200, changed.text
    shown = changed.json()["links"][0]
    assert shown["window"] == "from year 10 until year 20"
    assert shown["applies_until"] == "year 20"
    assert shown["applies_until_time"]["at"] == 20
    key = f"picture:{asset_id}"
    assert "may not apply then" in _item(_context(auth_client, w, scene), key)["text"]
    _when(auth_client, w, scene, 12)
    assert _item(_context(auth_client, w, scene), key) is not None
    _when(auth_client, w, scene, 25)
    assert _item(_context(auth_client, w, scene), key) is None


def test_a_link_can_show_one_version_of_its_picture(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container, describe=False)
    first = _view(auth_client, w, asset_id)["version"]
    made = _two_step(auth_client, w["base"], asset_id, _png("navy"), kind="picture")
    assert made["link"] is None and made["version"]["version"] == 2
    view = _view(auth_client, w, asset_id)
    assert view["version"]["version"] == 2
    key = f"picture:{asset_id}"
    item = _item(_context(auth_client, w, scene), key)
    assert made["version"]["id"] in str(item) and first["id"] not in str(item)
    (link,) = view["links"]
    pinned = auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={"version_id": first["id"], "expected_revision": view["revision"]},
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["links"][0]["shows_version"] == 1
    item = _item(_context(auth_client, w, scene), key)
    assert first["id"] in str(item) and made["version"]["id"] not in str(item)


def test_a_link_showing_a_retired_version_leaves_the_picture_out(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container, describe=False)
    first = _view(auth_client, w, asset_id)["version"]
    made = _two_step(auth_client, w["base"], asset_id, _png("navy"), kind="picture")
    view = _view(auth_client, w, asset_id)
    (link,) = view["links"]
    pinned = auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={"version_id": first["id"], "expected_revision": view["revision"]},
    )
    assert pinned.status_code == 200, pinned.text
    retired = auth_client.post(f"{w['base']}/assets/{asset_id}/versions/{first['id']}/retire")
    assert retired.status_code == 200, retired.text
    item = _item(_context(auth_client, w, scene), f"picture:{asset_id}")
    # Never the newest in place of the chosen one: left out, and said so.
    assert item is not None and "left out" in item["text"]
    assert "no longer shown" in item["reason"]
    assert made["version"]["id"] not in str(item) and item.get("media") in (None, {})


def test_a_link_cannot_be_set_to_show_a_deleted_file_or_not_a_picture(auth_client, fake_container):
    w, _, asset_id = _scene_with_picture(auth_client, fake_container, describe=False)
    first = _view(auth_client, w, asset_id)["version"]
    _two_step(auth_client, w["base"], asset_id, _png("navy"), kind="picture")
    view = _view(auth_client, w, asset_id)
    (link,) = view["links"]
    workspace, project = _ids(fake_container)
    store = fake_container.media_service._media
    key = (workspace, project, first["id"])
    original = store.versions[key]
    for broken in (
        original.model_copy(update={"deleted_at": utcnow()}),
        original.model_copy(update={"content_type": "application/pdf"}),
    ):
        store.versions[key] = broken
        refused = auth_client.put(
            f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
            json={"version_id": first["id"], "expected_revision": view["revision"]},
        )
        assert refused.status_code == 400, refused.text
    store.versions[key] = original


def _propose_coat(client, w, asset_id: str) -> dict:
    coat = next(
        item
        for item in _view(client, w, asset_id)["observations"]
        if item["text"] == "She wears a long blue coat."
    )
    offered = client.post(f"{w['base']}/observations/{coat['id']}/propose", json={})
    assert offered.status_code == 201, offered.text
    mutation = client.get(f"{w['base']}/mutations/{offered.json()['mutation_id']}").json()
    return next(
        item["payload"] for item in mutation["operations"] if item["payload"]["op"] == "assert_fact"
    )


def test_a_fact_seen_in_a_picture_is_true_for_the_time_the_picture_applies(
    auth_client, fake_container
):
    w, _, asset_id = _scene_with_picture(auth_client, fake_container)
    view = _view(auth_client, w, asset_id)
    (link,) = view["links"]
    changed = auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={
            "applies_from": {"kind": "at", "at": 10},
            "applies_until": {"kind": "at", "at": 20},
            "expected_revision": view["revision"],
        },
    )
    assert changed.status_code == 200, changed.text
    # The link taken off (with no window) no longer counts: only the one still shown.
    operation = _propose_coat(auth_client, w, asset_id)
    assert operation["starts"]["at"] == 10 and operation["ends"]["at"] == 20


def test_a_fact_seen_in_a_picture_made_secret_is_a_secret(auth_client, fake_container):
    w, _, asset_id = _scene_with_picture(auth_client, fake_container)
    view = _view(auth_client, w, asset_id)
    (link,) = view["links"]
    changed = auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={"secret": True, "known_by": [w["tobin"]], "expected_revision": view["revision"]},
    )
    assert changed.status_code == 200, changed.text
    # The plain link taken off says nothing any more: the fact is a secret like the picture.
    operation = _propose_coat(auth_client, w, asset_id)
    assert operation["visibility"] == "secret"
    assert operation.get("starts") is None and operation.get("ends") is None


def test_a_picture_on_a_scene_taken_out_of_the_plan_says_nothing_of_a_fact(
    auth_client, fake_container
):
    from backend.domain.identity import AccessScope, AuthMode
    from backend.domain.operations import RetirePlanItemOp
    from tests.contract.test_context import _setup, _world

    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    made = _add(auth_client, w["base"], scene["id"], "On the quay", "teal", scene=True).json()
    asset_id = made["link"]["asset_id"]
    view = _view(auth_client, w, asset_id)
    (link,) = view["links"]
    secret = auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={"secret": True, "known_by": [], "expected_revision": view["revision"]},
    )
    assert secret.status_code == 200, secret.text
    workspace, project = _ids(fake_container)
    scope = AccessScope(
        actor_user_id="usr_owner",
        auth_mode=AuthMode.LOCAL,
        workspace_id=workspace,
        project_id=project,
    )
    mutation = fake_container.media_service._mutation
    draft = asyncio.run(
        mutation.create_draft(
            scope,
            workspace_id=workspace,
            project_id=project,
            operations=[RetirePlanItemOp(id=scene["id"])],
            expected_head=_head(auth_client, w["base"]),
        )
    )
    asyncio.run(mutation.commit(scope, draft.id))
    asyncio.run(
        fake_container.observation_store.add(
            [
                MediaObservation(
                    workspace_id=workspace,
                    project_id=project,
                    id="obs_quay_coat",
                    asset_id=asset_id,
                    asset_version_id=made["version"]["id"],
                    kind=ObservationKind.APPEARANCE,
                    text="She wears a long blue coat.",
                    subject_record_type="entity",
                    subject_record_id=w["mara"],
                    state=ObservationState.ACCEPTED,
                )
            ]
        )
    )
    offered = auth_client.post(f"{w['base']}/observations/obs_quay_coat/propose", json={})
    assert offered.status_code == 201, offered.text
    operations = auth_client.get(f"{w['base']}/mutations/{offered.json()['mutation_id']}").json()[
        "operations"
    ]
    (fact,) = [item["payload"] for item in operations if item["payload"]["op"] == "assert_fact"]
    # The secret link on a scene no longer in the plan does not make the fact a secret.
    assert fact.get("visibility") is None


# ------------------------------------------------------------------- AST-008


def test_deleting_a_picture_removes_what_a_model_said_about_it(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    gone = _add(auth_client, base, mara, "Portrait", "teal").json()
    kept = _add(auth_client, base, mara, "Sketch", "navy").json()
    workspace, project = _ids(fake_container)
    store = fake_container.observation_store
    asyncio.run(
        store.add(
            [
                MediaObservation(
                    workspace_id=workspace,
                    project_id=project,
                    id=f"obs_{index}",
                    asset_id=made["link"]["asset_id"],
                    asset_version_id=made["version"]["id"],
                    kind=ObservationKind.CAPTION,
                    text=text,
                    state=state,
                )
                for index, (made, text, state) in enumerate(
                    [
                        (gone, "A woman in a green coat", ObservationState.ACCEPTED),
                        (gone, "A lamp", ObservationState.REJECTED),
                        (kept, "A boat", ObservationState.ACCEPTED),
                    ]
                )
            ]
        )
    )
    _take_off(auth_client, base, gone["link"]["id"])
    asset_id = gone["link"]["asset_id"]
    deleted = auth_client.post(f"{base}/assets/{asset_id}/delete")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["notes_removed"] == 2
    left = asyncio.run(store.all(workspace_id=workspace, project_id=project))
    assert [item.text for item in left] == ["A boat"]
    events = auth_client.get(f"{base}/assets/{asset_id}/lifecycle").json()["items"]
    removed = [event for event in events if event["action"] == "notes_removed"]
    assert removed and removed[0]["item_count"] == 2
    assert MediaLifecycleAction.NOTES_REMOVED.value == "notes_removed"
    assert "green coat" not in str(events)
