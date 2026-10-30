"""Pictures from the media page: streamed uploads, finding pictures by words, and deleting
one picture's files before its story (AST-002, AST-007, AST-008)."""

from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest

from backend.domain.media_observations import MediaObservation, ObservationKind, ObservationState
from backend.services.media_search import MediaSearchResult, PictureCandidate
from backend.services.media_service import (
    MAX_PICTURE_BYTES,
    PictureTooLarge,
    query_words,
    receive_picture,
)
from tests.contract.test_media_processing import _EMPTY_DEFINITION, _png, _run

pytestmark = pytest.mark.contract


def _story(client, container) -> tuple[str, str, str]:
    project_id = container.settings.AUTH_LOCAL_PROJECT_ID
    base = f"/api/v1/projects/{project_id}"
    draft = client.post(f"{base}/schemas/drafts", json={"definition": _EMPTY_DEFINITION})
    client.post(f"{base}/schemas/{draft.json()['version']['id']}/publish")
    mara = client.post(
        f"{base}/entities", json={"type": "Character", "name": "Mara", "expected_revision": 0}
    ).json()["id"]
    scene = client.post(
        f"{base}/plan", json={"kind": "scene", "title": "The Docks", "expected_revision": 1}
    ).json()["id"]
    return base, mara, scene


def _head(client, base: str) -> int:
    return int(client.get(f"{base}/projection").json()["head_revision"])


def _add(client, base: str, target: str, name: str, color: str, scene: bool = False, **headers):
    path = f"{base}/plan/{target}/pictures" if scene else f"{base}/entities/{target}/pictures"
    made = client.post(
        path,
        params={"expected_revision": _head(client, base), "name": name},
        content=_png(color),
        headers=headers,
    )
    return made


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


def test_an_upload_is_hashed_as_it_streams_and_refused_early() -> None:
    picture = _png()
    received = asyncio.run(receive_picture(_chunks(picture[:5], picture[5:])))
    assert received.sha256 == hashlib.sha256(picture).hexdigest()
    assert received.size == len(picture)
    with pytest.raises(PictureTooLarge):
        asyncio.run(receive_picture(_chunks(b""), declared_length=str(MAX_PICTURE_BYTES + 1)))

    read: list[int] = []

    async def endless():
        yield b"\x89PNG\r\n\x1a\n" + b"\x00" * 1024
        while True:
            read.append(1)
            yield b"\x00" * 1024 * 1024

    with pytest.raises(PictureTooLarge):
        asyncio.run(receive_picture(endless()))
    assert len(read) <= MAX_PICTURE_BYTES // (1024 * 1024) + 1

    later: list[int] = []

    async def not_a_picture():
        yield b"%PDF-1.7 not a picture"
        later.append(1)
        yield b"more"

    with pytest.raises(Exception, match="Only PNG, JPEG, and WebP"):
        asyncio.run(receive_picture(not_a_picture()))
    assert later == []  # refused from its first bytes, before the rest was read


def test_an_upload_says_what_arrived_and_a_wrong_digest_is_refused(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    picture = _png("navy")
    digest = base64.b64encode(hashlib.sha256(picture).digest()).decode()
    made = auth_client.post(
        f"{base}/entities/{mara}/pictures",
        params={"expected_revision": _head(auth_client, base)},
        content=picture,
        headers={"Content-Digest": f"sha-512=:AAAA:, sha-256=:{digest}:"},
    )
    assert made.status_code == 201, made.text
    assert made.json()["received"] == {
        "bytes": len(picture),
        "sha256": hashlib.sha256(picture).hexdigest(),
        "digest": "matched",
    }
    wrong = auth_client.post(
        f"{base}/entities/{mara}/pictures",
        params={"expected_revision": _head(auth_client, base)},
        content=_png("olive"),
        headers={"Content-Digest": f"sha-256=:{digest}:"},
    )
    assert wrong.status_code == 400 and "did not arrive whole" in wrong.json()["detail"]
    too_big = auth_client.post(
        f"{base}/entities/{mara}/pictures",
        params={"expected_revision": _head(auth_client, base)},
        content=b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_PICTURE_BYTES,
    )
    assert too_big.status_code == 413
    assert "8 MB" in too_big.json()["detail"]


def test_pictures_are_found_by_name_description_and_meaning(auth_client, fake_container):
    base, mara, scene = _story(auth_client, fake_container)
    harbor = _add(auth_client, base, scene, "Harbor at dusk", "navy", scene=True).json()
    portrait = _add(auth_client, base, mara, "Portrait of Mara", "teal").json()
    third = _add(auth_client, base, mara, "Sketch", "olive").json()
    workspace = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    asyncio.run(
        fake_container.observation_store.add(
            [
                MediaObservation(
                    workspace_id=workspace,
                    project_id=project,
                    id="obs_1",
                    asset_id=portrait["link"]["asset_id"],
                    asset_version_id=portrait["version"]["id"],
                    kind=ObservationKind.CAPTION,
                    text="A woman in a green coat by the harbor",
                    state=ObservationState.ACCEPTED,
                ),
                MediaObservation(
                    workspace_id=workspace,
                    project_id=project,
                    id="obs_2",
                    asset_id=third["link"]["asset_id"],
                    asset_version_id=third["version"]["id"],
                    kind=ObservationKind.CAPTION,
                    text="The harbor burning",
                    state=ObservationState.PROPOSED,
                ),
            ]
        )
    )

    found = auth_client.get(f"{base}/pictures/search", params={"q": "harbor"}).json()
    assert [(item["name"], item["matched"]) for item in found["items"]] == [
        ("Harbor at dusk", "name"),
        ("Portrait of Mara", "description"),
    ]
    # A description nobody reviewed is not searched.
    assert all(item["name"] != "Sketch" for item in found["items"])
    assert found["by_meaning"] is False and found["note"]
    both = auth_client.get(f"{base}/pictures/search", params={"q": "green harbor"}).json()
    assert [item["name"] for item in both["items"]] == ["Portrait of Mara"]

    class _Meaning:
        async def pictures_for(self, workspace_id, project_id, **kwargs):
            return MediaSearchResult(
                pictures=[
                    PictureCandidate(
                        asset_id=third["link"]["asset_id"],
                        asset_version_id=None,
                        segment_id=None,
                        reason="similar",
                        score=0.71,
                        matched="picture",
                    ),
                    PictureCandidate(
                        asset_id="asset_from_elsewhere",
                        asset_version_id=None,
                        segment_id=None,
                        reason="similar",
                        score=0.9,
                        matched="picture",
                    ),
                ],
                semantic=True,
            )

    fake_container.media_service.search = _Meaning()
    meant = auth_client.get(f"{base}/pictures/search", params={"q": "fire at sea"}).json()
    assert [(item["name"], item["reason"], item["score"]) for item in meant["items"]] == [
        ("Sketch", "meaning", 0.71)
    ]
    assert meant["by_meaning"] is True and meant["note"] is None
    assert harbor["link"]["asset_id"]
    blank = auth_client.get(f"{base}/pictures/search", params={"q": "?"})
    assert blank.status_code == 400
    assert query_words("The  harbor, the HARBOR!") == ["the", "harbor"]


def test_a_picture_shown_nowhere_can_have_its_files_deleted(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait of Mara", "teal").json()
    asset_id, version = made["link"]["asset_id"], made["version"]
    _run(fake_container)  # its smaller copies are made
    workspace = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    used = auth_client.get(f"{base}/pictures").json()["storage"]["used_bytes"]
    forgotten: list[list[str]] = []

    async def forget(_w, _p, version_ids):
        forgotten.append(version_ids)

    fake_container.media_service.on_versions_removed(forget)

    shown = auth_client.post(f"{base}/assets/{asset_id}/delete")
    assert shown.status_code == 409 and "one entry or scene" in shown.json()["detail"]

    off = auth_client.post(
        f"{base}/links/{made['link']['id']}/retire",
        json={"expected_revision": _head(auth_client, base)},
    )
    assert off.status_code == 200, off.text
    deleted = auth_client.post(f"{base}/assets/{asset_id}/delete")
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["lifecycle"] == "deleted" and body["files_removed"] == 1
    assert body["copies_removed"] == 2 and body["bytes_freed"] == version["byte_length"]
    assert forgotten == [[version["id"]]]

    objects = fake_container.objects
    assert (
        objects.stat(
            version["object_key"],
            workspace_id=workspace,
            project_id=fake_container.settings.AUTH_LOCAL_PROJECT_ID,
        )
        is None
    )
    content = auth_client.get(f"{base}/assets/{asset_id}/versions/{version['id']}/content")
    assert content.status_code == 404
    assert all(
        item["asset_id"] != asset_id for item in auth_client.get(f"{base}/pictures").json()["items"]
    )
    after = auth_client.get(f"{base}/pictures").json()["storage"]["used_bytes"]
    assert after < used

    # The tombstone: the record and its checksum stay, with who deleted it and when.
    versions = auth_client.get(f"{base}/assets/{asset_id}/versions").json()["items"]
    assert versions[0]["checksum"] == version["checksum"] and versions[0]["deleted_at"]
    actions = [
        e["action"] for e in auth_client.get(f"{base}/assets/{asset_id}/lifecycle").json()["items"]
    ]
    assert actions[-2:] == ["original_deleted", "asset_deleted"]
    again = auth_client.post(f"{base}/assets/{asset_id}/delete")
    assert again.status_code == 404

    archive = asyncio.run(
        fake_container.archive_service.export_project(
            workspace, fake_container.settings.AUTH_LOCAL_PROJECT_ID
        )
    )
    assert archive["objects"] == []
    (exported,) = archive["media"]["versions"]
    assert exported["deleted_at"] is not None


def test_a_picture_shown_in_a_what_if_is_not_deleted(auth_client, fake_container):
    base, mara, _ = _story(auth_client, fake_container)
    made = _add(auth_client, base, mara, "Portrait", "teal").json()
    what_if = auth_client.post(f"{base}/what-ifs", json={"name": "Keep it"}).json()["id"]
    off = auth_client.post(
        f"{base}/links/{made['link']['id']}/retire",
        json={"expected_revision": _head(auth_client, base)},
    )
    assert off.status_code == 200, off.text
    refused = auth_client.post(f"{base}/assets/{made['link']['asset_id']}/delete")
    assert refused.status_code == 409, refused.text
    assert what_if
