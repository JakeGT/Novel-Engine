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


def test_revisions_record_history_and_tags(auth_client) -> None:
    project = "local-project"
    _publish_schema(auth_client, project)

    created = auth_client.post(
        f"/api/v1/projects/{project}/entities",
        json={"type": "Character", "name": "Mara", "attributes": {}, "expected_revision": 0},
    )
    assert created.status_code == 201, created.text
    entity_id = created.json()["id"]

    revisions = auth_client.get(f"/api/v1/projects/{project}/revisions")
    assert revisions.status_code == 200, revisions.text
    items = revisions.json()["items"]
    assert items and items[0]["operation_count"] == 1

    history = auth_client.get(f"/api/v1/projects/{project}/entities/{entity_id}/history")
    assert history.status_code == 200, history.text
    assert [entry["operation_type"] for entry in history.json()["items"]] == ["create_entity"]

    at_revision = auth_client.get(f"/api/v1/projects/{project}/entities/{entity_id}/at-revision/1")
    assert at_revision.status_code == 200, at_revision.text
    assert at_revision.json()["name"] == "Mara"

    tag = auth_client.post(f"/api/v1/projects/{project}/tags", json={"name": "v1", "revision": 1})
    assert tag.status_code == 201, tag.text
    assert tag.json()["revision"] == 1

    listed = auth_client.get(f"/api/v1/projects/{project}/tags")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "v1"

    deleted = auth_client.delete(f"/api/v1/projects/{project}/tags/v1")
    assert deleted.status_code == 204


def test_revisions_require_auth(api_client) -> None:
    response = api_client.get("/api/v1/projects/local-project/revisions")
    assert response.status_code == 401


def test_revision_diff_and_fact_at_revision(auth_client) -> None:
    project = "local-project"
    _publish_schema(auth_client, project)

    created = auth_client.post(
        f"/api/v1/projects/{project}/entities",
        json={"type": "Character", "name": "Mara", "attributes": {}, "expected_revision": 0},
    )
    assert created.status_code == 201, created.text
    entity_id = created.json()["id"]

    fact = auth_client.post(
        f"/api/v1/projects/{project}/facts",
        json={
            "proposition": {
                "subject_id": entity_id,
                "predicate": "HAS_NAME",
                "object": {"kind": "string", "value": "Mara"},
            },
            "expected_revision": 1,
        },
    )
    assert fact.status_code == 201, fact.text
    fact_id = fact.json()["id"]

    diff = auth_client.get(f"/api/v1/projects/{project}/revisions/1/diff/2")
    assert diff.status_code == 200, diff.text
    assert any(entry["record_type"] == "fact" for entry in diff.json()["items"])

    at_revision = auth_client.get(f"/api/v1/projects/{project}/facts/{fact_id}/at-revision/2")
    assert at_revision.status_code == 200, at_revision.text
    assert at_revision.json()["status"] == "asserted"


def test_revision_list_pages_back_from_the_newest_revision(auth_client) -> None:
    project = "local-project"
    _publish_schema(auth_client, project)
    for index in range(6):
        created = auth_client.post(
            f"/api/v1/projects/{project}/entities",
            json={
                "type": "Character",
                "name": f"Person {index}",
                "attributes": {},
                "expected_revision": index,
            },
        )
        assert created.status_code == 201, created.text

    def page(**params) -> list[int]:
        response = auth_client.get(f"/api/v1/projects/{project}/revisions", params=params)
        assert response.status_code == 200, response.text
        return [item["revision"] for item in response.json()["items"]]

    # A short page is the newest revisions, not the oldest.
    assert page(limit=2) == [6, 5]
    assert page(limit=2, offset=2) == [4, 3]
    assert page(limit=4, offset=4) == [2, 1]
    assert page(limit=5, offset=6) == []
    assert page() == [6, 5, 4, 3, 2, 1]
