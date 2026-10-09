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


def _project_id(container) -> str:
    return container.settings.AUTH_LOCAL_PROJECT_ID


def test_canon_entity_create_read_edit_retire(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    base = f"/api/v1/projects/{project_id}/entities"

    created = auth_client.post(
        base,
        json={
            "type": "Character",
            "name": "Aria",
            "attributes": {"role": "scout"},
            "expected_revision": 0,
        },
    )
    assert created.status_code == 201, created.text
    entity = created.json()
    entity_id = entity["id"]
    assert entity["name"] == "Aria"
    assert entity["lifecycle"] == "active"
    assert entity["attributes"] == {"role": "scout"}

    read = auth_client.get(f"{base}/{entity_id}")
    assert read.status_code == 200
    assert read.json()["name"] == "Aria"

    edited = auth_client.patch(
        f"{base}/{entity_id}", json={"name": "Aria II", "expected_revision": 1}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Aria II"

    # Retirement is destructive: without an explicit acknowledgement it is parked
    # for review instead of committing.
    parked = auth_client.post(f"{base}/{entity_id}/retire", json={"expected_revision": 2})
    assert parked.status_code == 409, parked.text
    assert parked.json()["code"] == "review_required"
    assert parked.json()["field_errors"]["reasons"] == "destructive_operation"
    assert auth_client.get(f"{base}/{entity_id}").json()["lifecycle"] == "active"

    retired = auth_client.post(
        f"{base}/{entity_id}/retire",
        json={"expected_revision": 2, "acknowledge_high_impact": True},
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["lifecycle"] == "retired"

    active_ids = [item["id"] for item in auth_client.get(base).json()["items"]]
    assert entity_id not in active_ids
    all_items = auth_client.get(f"{base}?include_retired=true").json()["items"]
    assert entity_id in [item["id"] for item in all_items]


def test_canon_entity_stale_revision_conflicts(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    base = f"/api/v1/projects/{project_id}/entities"

    created = auth_client.post(
        base, json={"type": "Character", "name": "Bram", "attributes": {}, "expected_revision": 0}
    )
    assert created.status_code == 201, created.text
    entity_id = created.json()["id"]

    stale = auth_client.patch(
        f"{base}/{entity_id}", json={"name": "Bram II", "expected_revision": 0}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"


def test_canon_entity_unknown_type_rejected(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    base = f"/api/v1/projects/{project_id}/entities"

    response = auth_client.post(
        base,
        json={"type": "NotAType", "name": "X", "attributes": {}, "expected_revision": 0},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_canon_entity_search(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    base = f"/api/v1/projects/{project_id}/entities"

    auth_client.post(
        base,
        json={
            "type": "Character",
            "name": "Searchable One",
            "attributes": {},
            "expected_revision": 0,
        },
    )
    results = auth_client.get(f"{base}/search?q=searchable").json()["items"]
    assert [item["name"] for item in results] == ["Searchable One"]


def test_published_custom_type_and_attributes_are_enforced(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    definition = {
        "label": "Ships",
        "entity_extensions": [
            {
                "entity_type": "Ship",
                "fields": [
                    {"name": "hull", "field_type": "string", "required": True},
                    {"name": "crew", "field_type": "integer", "minimum": 0},
                ],
            }
        ],
        "predicate_extensions": [],
    }
    draft = auth_client.post(
        f"/api/v1/projects/{project_id}/schemas/drafts", json={"definition": definition}
    )
    assert draft.status_code == 201, draft.text
    version_id = draft.json()["version"]["id"]
    assert (
        auth_client.post(f"/api/v1/projects/{project_id}/schemas/{version_id}/publish").status_code
        == 200
    )
    base = f"/api/v1/projects/{project_id}/entities"

    created = auth_client.post(
        base,
        json={
            "type": "Ship",
            "name": "Wayfarer",
            "attributes": {"hull": "oak", "crew": 12},
            "expected_revision": 0,
        },
    )
    assert created.status_code == 201, created.text

    invalid = auth_client.post(
        base,
        json={
            "type": "Ship",
            "name": "Broken",
            "attributes": {"crew": -1},
            "expected_revision": 1,
        },
    )
    assert invalid.status_code == 422, invalid.text
    assert "invalid_attributes" in invalid.json()["detail"]
