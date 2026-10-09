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
