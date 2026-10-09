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


def _revision(response) -> int:
    return int(response.headers["ETag"].strip('"'))


def _create_entity(
    client, project: str, *, entity_type: str, name: str, expected_revision: int
) -> str:
    response = client.post(
        f"/api/v1/projects/{project}/entities",
        json={
            "type": entity_type,
            "name": name,
            "attributes": {},
            "expected_revision": expected_revision,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _entity_ref(entity_id: str, project: str) -> dict:
    return {"kind": "entity_ref", "entity_id": entity_id, "project_id": project}


def test_facts_respect_cardinality(auth_client, fake_container) -> None:
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    _publish_schema(auth_client, project)
    char = _create_entity(
        auth_client, project, entity_type="Character", name="Arin", expected_revision=0
    )
    location = _create_entity(
        auth_client, project, entity_type="Location", name="Harbor", expected_revision=1
    )

    response = auth_client.post(
        f"/api/v1/projects/{project}/facts",
        json={
            "proposition": {
                "subject_id": char,
                "predicate": "HAS_NAME",
                "object": {"kind": "string", "value": "Arin"},
            },
            "expected_revision": 2,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["predicate"] == "HAS_NAME"
    assert _revision(response) == 3

    response = auth_client.post(
        f"/api/v1/projects/{project}/facts",
        json={
            "proposition": {
                "subject_id": char,
                "predicate": "LOCATED_AT",
                "object": _entity_ref(location, project),
            },
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201, response.text
    assert _revision(response) == 4

    conflict = auth_client.post(
        f"/api/v1/projects/{project}/facts",
        json={
            "proposition": {
                "subject_id": char,
                "predicate": "LOCATED_AT",
                "object": _entity_ref(location, project),
            },
            "expected_revision": 4,
        },
    )
    assert conflict.status_code == 409, conflict.text

    invalid = auth_client.post(
        f"/api/v1/projects/{project}/facts",
        json={
            "proposition": {
                "subject_id": char,
                "predicate": "NOT_A_PREDICATE",
                "object": {"kind": "string", "value": "x"},
            },
            "expected_revision": 4,
        },
    )
    assert invalid.status_code == 400, invalid.text


def test_belief_truth_agreement_is_derived(auth_client, fake_container) -> None:
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    _publish_schema(auth_client, project)
    char = _create_entity(
        auth_client, project, entity_type="Character", name="Mara", expected_revision=0
    )
    location = _create_entity(
        auth_client, project, entity_type="Location", name="Tower", expected_revision=1
    )

    fact = auth_client.post(
        f"/api/v1/projects/{project}/facts",
        json={
            "proposition": {
                "subject_id": location,
                "predicate": "HAS_POPULATION",
                "object": {"kind": "integer", "value": 100},
            },
            "expected_revision": 2,
        },
    )
    assert fact.status_code == 201, fact.text

    belief = auth_client.post(
        f"/api/v1/projects/{project}/beliefs",
        json={
            "holder_entity_id": char,
            "proposition": {
                "subject_id": location,
                "predicate": "HAS_POPULATION",
                "object": {"kind": "integer", "value": 5},
            },
            "source_ref": "rumor at the docks",
            "expected_revision": 3,
        },
    )
    assert belief.status_code == 201, belief.text
    belief_id = belief.json()["id"]
    assert belief.json()["source_ref"] == "rumor at the docks"
    assert _revision(belief) == 4

    agreement = auth_client.get(f"/api/v1/projects/{project}/beliefs/{belief_id}/truth-agreement")
    assert agreement.status_code == 200
    assert agreement.json()["truth_agreement"] == "disagrees"

    unknown = auth_client.post(
        f"/api/v1/projects/{project}/beliefs",
        json={
            "holder_entity_id": char,
            "proposition": {
                "subject_id": location,
                "predicate": "HAS_AREA",
                "object": {"kind": "quantity", "value": 12, "unit": "km2"},
            },
            "source_ref": "guess",
            "expected_revision": 4,
        },
    )
    assert unknown.status_code == 201, unknown.text
    unknown_id = unknown.json()["id"]
    agreement = auth_client.get(f"/api/v1/projects/{project}/beliefs/{unknown_id}/truth-agreement")
    assert agreement.json()["truth_agreement"] == "unknown"

    missing_source = auth_client.post(
        f"/api/v1/projects/{project}/beliefs",
        json={
            "holder_entity_id": char,
            "proposition": {
                "subject_id": location,
                "predicate": "HAS_POPULATION",
                "object": {"kind": "integer", "value": 1},
            },
            "source_ref": "",
            "expected_revision": 5,
        },
    )
    assert missing_source.status_code == 400, missing_source.text


def test_events_store_fuzzy_world_position(auth_client, fake_container) -> None:
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    _publish_schema(auth_client, project)
    char = _create_entity(
        auth_client, project, entity_type="Character", name="Ivo", expected_revision=0
    )
    location = _create_entity(
        auth_client, project, entity_type="Location", name="Lantern", expected_revision=1
    )

    response = auth_client.post(
        f"/api/v1/projects/{project}/events",
        json={
            "kind": "fire",
            "name": "The Harbor Fire",
            "participants": [{"entity_id": char, "role": "witness"}],
            "location_ids": [location],
            "world_position": "sometime before the pact",
            "expected_revision": 2,
        },
    )
    assert response.status_code == 201, response.text
    event = response.json()
    assert event["world_position"] == "sometime before the pact"
    assert event["participants"][0]["role"] == "witness"

    invalid = auth_client.post(
        f"/api/v1/projects/{project}/events",
        json={
            "kind": "fire",
            "name": "Bad Event",
            "participants": [{"entity_id": "entity_missing", "role": "witness"}],
            "expected_revision": _revision(response),
        },
    )
    assert invalid.status_code == 404, invalid.text


def test_published_custom_predicate_is_enforced(auth_client, fake_container) -> None:
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    definition = {
        "label": "Character motto",
        "entity_extensions": [],
        "predicate_extensions": [
            {
                "predicate": "HAS_MOTTO",
                "subject_types": ["Character"],
                "object_kind": "string",
                "cardinality": "one",
                "temporal_allowed": False,
            }
        ],
    }
    draft = auth_client.post(
        f"/api/v1/projects/{project}/schemas/drafts", json={"definition": definition}
    )
    assert draft.status_code == 201, draft.text
    version_id = draft.json()["version"]["id"]
    assert (
        auth_client.post(f"/api/v1/projects/{project}/schemas/{version_id}/publish").status_code
        == 200
    )
    char = _create_entity(
        auth_client, project, entity_type="Character", name="Mara", expected_revision=0
    )

    created = auth_client.post(
        f"/api/v1/projects/{project}/facts",
        json={
            "proposition": {
                "subject_id": char,
                "predicate": "HAS_MOTTO",
                "object": {"kind": "string", "value": "Keep walking"},
            },
            "expected_revision": 1,
        },
    )
    assert created.status_code == 201, created.text

    conflict = auth_client.post(
        f"/api/v1/projects/{project}/facts",
        json={
            "proposition": {
                "subject_id": char,
                "predicate": "HAS_MOTTO",
                "object": {"kind": "string", "value": "Never turn back"},
            },
            "expected_revision": 2,
        },
    )
    assert conflict.status_code == 409, conflict.text
