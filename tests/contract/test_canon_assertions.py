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


def test_facts_and_events_can_be_listed_by_the_entity_they_reference(
    auth_client, fake_container
) -> None:
    project_id = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    base = f"/api/v1/projects/{project_id}"
    draft = auth_client.post(
        f"{base}/schemas/drafts",
        json={"definition": {"label": "core", "entity_extensions": [], "predicate_extensions": []}},
    ).json()["version"]["id"]
    assert auth_client.post(f"{base}/schemas/{draft}/publish").status_code == 200

    def entity(kind: str, name: str, revision: int) -> str:
        created = auth_client.post(
            f"{base}/entities", json={"type": kind, "name": name, "expected_revision": revision}
        )
        assert created.status_code == 201, created.text
        return created.json()["id"]

    mara = entity("Character", "Mara", 0)
    iven = entity("Character", "Iven", 1)
    harbor = entity("Location", "Harbor", 2)
    for revision, subject in ((3, mara), (4, iven)):
        fact = auth_client.post(
            f"{base}/facts",
            json={
                "proposition": {
                    "subject_id": subject,
                    "predicate": "LOCATED_AT",
                    "object": {"kind": "entity_ref", "entity_id": harbor, "project_id": project_id},
                },
                "expected_revision": revision,
            },
        )
        assert fact.status_code == 201, fact.text
    event = auth_client.post(
        f"{base}/events",
        json={
            "kind": "arrival",
            "name": "Arrival",
            "expected_revision": 5,
            "participants": [{"entity_id": mara, "role": "traveller"}],
            "location_ids": [harbor],
        },
    )
    assert event.status_code == 201, event.text

    at_harbor = auth_client.get(f"{base}/facts?object_entity_id={harbor}").json()["items"]
    assert sorted(fact["subject_id"] for fact in at_harbor) == sorted([mara, iven])
    assert auth_client.get(f"{base}/facts?object_entity_id={mara}").json()["items"] == []

    for involved in (mara, harbor):
        events = auth_client.get(f"{base}/events?entity_id={involved}").json()["items"]
        assert [item["name"] for item in events] == ["Arrival"]
    assert auth_client.get(f"{base}/events?entity_id={iven}").json()["items"] == []

    # Retired events are excluded unless asked for, on every store.
    retired = auth_client.post(
        f"{base}/events/{event.json()['id']}/retire",
        json={"expected_revision": 6, "acknowledge_high_impact": True},
    )
    assert retired.status_code == 200, retired.text
    assert auth_client.get(f"{base}/events?entity_id={mara}").json()["items"] == []
    assert (
        len(auth_client.get(f"{base}/events?entity_id={mara}&include_retired=true").json()["items"])
        == 1
    )


def test_local_session_reports_its_single_project(auth_client, fake_container) -> None:
    me = auth_client.get("/auth/me").json()
    assert me["auth_mode"] == "local"
    assert me["local"] == {
        "workspace_id": fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
        "project_id": fake_container.settings.AUTH_LOCAL_PROJECT_ID,
    }


def test_hosted_session_does_not_name_a_local_project(hosted_client, hosted_container) -> None:
    from tests.contract.test_workspace_scope import _hosted_login

    _hosted_login(hosted_client, hosted_container)
    assert "local" not in hosted_client.get("/auth/me").json()
