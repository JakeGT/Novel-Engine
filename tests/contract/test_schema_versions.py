import pytest

pytestmark = pytest.mark.contract

BASE = "/api/v1/projects/local-project/schemas"

DEFINITION = {
    "label": "Ship extension",
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


def test_schema_versions_require_session(api_client) -> None:
    assert api_client.get(BASE).status_code == 401


def test_validate_endpoint(auth_client) -> None:
    response = auth_client.post(f"{BASE}/validate", json={"definition": DEFINITION})
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["content_hash"]
    assert body["entity_types"] == ["Ship"]


def test_draft_publish_flow(auth_client) -> None:
    assert auth_client.get(BASE).json() == []

    draft = auth_client.post(f"{BASE}/drafts", json={"definition": DEFINITION})
    assert draft.status_code == 201
    body = draft.json()
    assert body["compatibility"] == "additive"
    version_id = body["version"]["id"]
    assert body["version"]["status"] == "draft"

    published = auth_client.post(f"{BASE}/{version_id}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    listed = auth_client.get(BASE).json()
    assert len(listed) == 1
    assert listed[0]["id"] == version_id


def test_invalid_definition_rejected(auth_client) -> None:
    bad = {
        "definition": {
            "label": "x",
            "entity_extensions": [{"entity_type": "1bad", "fields": []}],
            "predicate_extensions": [],
        }
    }
    response = auth_client.post(f"{BASE}/drafts", json=bad)
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
