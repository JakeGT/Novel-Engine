"""Caller permissions, schema vocabulary, and local project scoping."""

import pytest

from tests.contract.test_canon_entities import _project_id, _publish_schema
from tests.contract.test_workspace_scope import _create_project, _create_workspace, _hosted_login

pytestmark = pytest.mark.contract

EXTENDED = {
    "label": "with ships",
    "description": "",
    "entity_extensions": [
        {
            "entity_type": "Ship",
            "description": "A vessel",
            "fields": [
                {"name": "tonnage", "field_type": "integer", "required": True},
                {"name": "rig", "field_type": "enum", "values": ["sloop", "brig"]},
            ],
        }
    ],
    "predicate_extensions": [
        {
            "predicate": "CAPTAINS",
            "subject_types": ["Character"],
            "object_kind": "entity_ref",
            "object_types": ["Ship"],
            "cardinality": "many",
        }
    ],
}


def test_vocabulary_lists_core_and_published_extensions(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    base = f"/api/v1/projects/{project_id}/schemas"

    core_only = auth_client.get(f"{base}/vocabulary")
    assert core_only.status_code == 200, core_only.text
    body = core_only.json()
    assert body["schema_version_id"] is None
    names = {item["name"] for item in body["entity_types"]}
    assert {"Character", "Location", "Faction"} <= names
    assert all(item["core"] for item in body["entity_types"])
    located = next(p for p in body["predicates"] if p["predicate"] == "LOCATED_AT")
    assert located["object_shape"] == "entity_ref" and located["object_types"] == ["Location"]
    assert located["cardinality"] == "one" and located["temporal_allowed"] is True

    draft = auth_client.post(f"{base}/drafts", json={"definition": EXTENDED})
    assert draft.status_code == 201, draft.text
    version = draft.json()["version"]
    assert version["definition"]["label"] == "with ships"
    # A draft changes nothing until it is published.
    assert auth_client.get(f"{base}/vocabulary").json()["schema_version_id"] is None
    assert auth_client.post(f"{base}/{version['id']}/publish").status_code == 200

    extended = auth_client.get(f"{base}/vocabulary").json()
    assert extended["schema_version_id"] == version["id"]
    ship = next(item for item in extended["entity_types"] if item["name"] == "Ship")
    assert ship["core"] is False
    assert "tonnage" in ship["attributes_schema"]["properties"]
    captains = next(p for p in extended["predicates"] if p["predicate"] == "CAPTAINS")
    assert captains["core"] is False and captains["object_types"] == ["Ship"]


def test_schema_versions_expose_their_definition(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    versions = auth_client.get(f"/api/v1/projects/{project_id}/schemas").json()
    assert versions[0]["definition"]["label"] == "core"


def test_local_mode_access_is_owner_and_other_projects_are_not_found(
    auth_client, fake_container
) -> None:
    project_id = _project_id(fake_container)
    access = auth_client.get(f"/api/v1/projects/{project_id}/access")
    assert access.status_code == 200, access.text
    body = access.json()
    assert body["role"] == "owner" and body["can_write"] and body["can_admin"]
    assert body["hosted"] is False

    # Local mode has one project: another id in the URL is never silently
    # served from (or written to) the local project's scope.
    for path in ("entities", "mutations", "revisions", "access", "projection"):
        assert auth_client.get(f"/api/v1/projects/prj_someone_else/{path}").status_code == 404
    created = auth_client.post(
        "/api/v1/projects/prj_someone_else/entities",
        json={"type": "Character", "name": "X", "expected_revision": 0},
    )
    assert created.status_code == 404


def test_hosted_access_reflects_the_members_role(hosted_client, hosted_container) -> None:
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "access")
    project_id = _create_project(hosted_client, csrf, workspace_id, "proj")
    headers = {"X-Workspace-Id": workspace_id}

    owner = hosted_client.get(f"/api/v1/projects/{project_id}/access", headers=headers).json()
    assert owner["role"] == "owner" and owner["can_admin"] is True and owner["hosted"] is True

    assert (
        hosted_client.get(
            f"/api/v1/projects/{project_id}/access", headers={"X-Workspace-Id": "ws_not_mine"}
        ).status_code
        == 403
    )
