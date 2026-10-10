import pytest

pytestmark = pytest.mark.contract

_EMPTY_DEFINITION = {
    "label": "core",
    "description": "",
    "entity_extensions": [],
    "predicate_extensions": [],
}


def _publish_schema(client, project_id: str) -> None:
    draft = client.post(
        f"/api/v1/projects/{project_id}/schemas/drafts",
        json={"definition": _EMPTY_DEFINITION},
    )
    assert draft.status_code == 201, draft.text
    version_id = draft.json()["version"]["id"]
    published = client.post(f"/api/v1/projects/{project_id}/schemas/{version_id}/publish")
    assert published.status_code == 200, published.text


def test_media_asset_version_and_link_flow(auth_client, fake_container) -> None:
    project_id = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    workspace_id = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    _publish_schema(auth_client, project_id)

    entity = auth_client.post(
        f"/api/v1/projects/{project_id}/entities",
        json={
            "type": "Location",
            "name": "Harbor",
            "attributes": {},
            "expected_revision": 0,
        },
    )
    assert entity.status_code == 201, entity.text
    entity_id = entity.json()["id"]

    asset = auth_client.post(f"/api/v1/projects/{project_id}/assets", json={"name": "Harbor map"})
    assert asset.status_code == 201, asset.text
    asset_id = asset.json()["id"]

    stat = fake_container.objects.put(
        b"PNG", workspace_id=workspace_id, project_id=project_id, content_type="image/png"
    )
    version = auth_client.post(
        f"/api/v1/projects/{project_id}/assets/{asset_id}/versions",
        json={"object_key": stat.key, "attribution": "seed"},
    )
    assert version.status_code == 201, version.text
    version_body = version.json()
    assert version_body["checksum"] == stat.checksum
    assert version_body["content_type"] == "image/png"

    link = auth_client.post(
        f"/api/v1/projects/{project_id}/assets/{asset_id}/links",
        json={
            "asset_version_id": version_body["id"],
            "target_id": entity_id,
            "role": "EVIDENCE_FOR",
            "expected_revision": 1,
        },
    )
    assert link.status_code == 201, link.text
    link_body = link.json()
    assert link_body["role"] == "EVIDENCE_FOR"
    assert link_body["target_record_id"] == entity_id

    listing = auth_client.get(f"/api/v1/projects/{project_id}/assets/{asset_id}/links")
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1

    retired = auth_client.post(
        f"/api/v1/projects/{project_id}/links/{link_body['id']}/retire",
        json={"expected_revision": 2},
    )
    assert retired.status_code == 200, retired.text

    after = auth_client.get(f"/api/v1/projects/{project_id}/assets/{asset_id}/links")
    assert after.json()["items"] == []

    retired_version = auth_client.post(
        f"/api/v1/projects/{project_id}/assets/{asset_id}/versions/{version_body['id']}/retire"
    )
    assert retired_version.status_code == 200, retired_version.text
    assert retired_version.json()["retired_revision"] is not None

    retired_asset = auth_client.post(f"/api/v1/projects/{project_id}/assets/{asset_id}/retire")
    assert retired_asset.status_code == 200, retired_asset.text
    assert retired_asset.json()["lifecycle"] == "retired"
    assert retired_asset.json()["retired_revision"] is not None


def test_media_requires_session(api_client, fake_container) -> None:
    project_id = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    response = api_client.get(f"/api/v1/projects/{project_id}/assets")
    assert response.status_code == 401


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16


def _entry(client, project_id: str, name: str = "Mara", revision: int = 0) -> str:
    created = client.post(
        f"/api/v1/projects/{project_id}/entities",
        json={"type": "Character", "name": name, "expected_revision": revision},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_a_picture_is_uploaded_linked_to_an_entry_and_served_back(
    auth_client, fake_container
) -> None:
    project_id = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    base = f"/api/v1/projects/{project_id}"
    _publish_schema(auth_client, project_id)
    entity_id = _entry(auth_client, project_id)

    upload = auth_client.post(
        f"{base}/entities/{entity_id}/pictures",
        params={"expected_revision": 1, "name": "  Mara   at the helm  "},
        content=PNG,
        # What the client claims is ignored; the bytes decide.
        headers={"Content-Type": "application/octet-stream"},
    )

    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert upload.headers["ETag"] == '"2"'
    assert body["version"]["content_type"] == "image/png"
    assert body["link"]["role"] == "DEPICTS" and body["link"]["target_record_id"] == entity_id
    asset_id, version_id = body["link"]["asset_id"], body["version"]["id"]
    assert body["link"]["asset_version_id"] == version_id

    # It appears among the entry's links and in the media list, under a tidy name.
    links = auth_client.get(f"{base}/links", params={"target_id": entity_id}).json()["items"]
    assert [link["asset_id"] for link in links] == [asset_id]
    assets = auth_client.get(f"{base}/assets").json()["items"]
    assert [asset["name"] for asset in assets] == ["Mara at the helm"]

    content = auth_client.get(f"{base}/assets/{asset_id}/versions/{version_id}/content")
    assert content.status_code == 200 and content.content == PNG
    assert content.headers["content-type"] == "image/png"
    assert content.headers["Cache-Control"].startswith("private")
    assert content.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in content.headers["Content-Security-Policy"]

    # JPEG and WebP are accepted too.
    for index, data in enumerate((JPEG, WEBP)):
        more = auth_client.post(
            f"{base}/entities/{entity_id}/pictures",
            params={"expected_revision": 2 + index},
            content=data,
        )
        assert more.status_code == 201, more.text
    assert (
        len(auth_client.get(f"{base}/links", params={"target_id": entity_id}).json()["items"]) == 3
    )

    # Removing a picture retires the link; the history stays.
    retired = auth_client.post(
        f"{base}/links/{body['link']['id']}/retire", json={"expected_revision": 4}
    )
    assert retired.status_code == 200, retired.text
    remaining = auth_client.get(f"{base}/links", params={"target_id": entity_id}).json()["items"]
    assert len(remaining) == 2


def test_picture_uploads_are_checked_and_scoped(auth_client, fake_container) -> None:
    project_id = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    base = f"/api/v1/projects/{project_id}"
    _publish_schema(auth_client, project_id)
    entity_id = _entry(auth_client, project_id)
    url = f"{base}/entities/{entity_id}/pictures"

    # Not an image, whatever it calls itself.
    script = auth_client.post(
        url,
        params={"expected_revision": 1},
        content=b"<svg onload=alert(1)>",
        headers={"Content-Type": "image/png"},
    )
    assert script.status_code == 400 and "PNG, JPEG, and WebP" in script.json()["detail"]
    assert auth_client.post(url, params={"expected_revision": 1}, content=b"").status_code == 400

    from backend.services.media_service import MAX_PICTURE_BYTES

    huge = auth_client.post(
        url, params={"expected_revision": 1}, content=PNG + b"\x00" * MAX_PICTURE_BYTES
    )
    assert huge.status_code == 400 and "too large" in huge.json()["detail"]

    missing = auth_client.post(
        f"{base}/entities/ent_nope/pictures", params={"expected_revision": 1}, content=PNG
    )
    assert missing.status_code == 404
    # Nothing was stored or linked by any of the refused uploads.
    assert auth_client.get(f"{base}/assets").json()["items"] == []
    assert auth_client.get(f"{base}/links").json()["items"] == []

    # A stale head is a conflict, not a silent overwrite.
    stale = auth_client.post(url, params={"expected_revision": 0}, content=PNG)
    assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"

    ok = auth_client.post(url, params={"expected_revision": 1}, content=PNG).json()
    # A version must belong to the asset in the address.
    wrong = auth_client.get(f"{base}/assets/ast_other/versions/{ok['version']['id']}/content")
    assert wrong.status_code == 404


def test_pictures_require_a_session(api_client) -> None:
    base = "/api/v1/projects/local-project"
    upload = api_client.post(
        f"{base}/entities/ent_x/pictures", params={"expected_revision": 0}, content=PNG
    )
    assert upload.status_code == 401
    assert api_client.get(f"{base}/assets/ast_x/versions/asv_x/content").status_code == 401
