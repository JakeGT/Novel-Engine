from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.contract


def _hosted_login(client, container) -> str:
    login = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    client.get(f"/auth/callback?state={state}&code=code-123", follow_redirects=False)
    csrf = client.cookies.get(container.settings.CSRF_COOKIE_NAME)
    assert csrf
    return csrf


def _create_workspace(client, csrf: str, slug: str) -> str:
    response = client.post(
        "/api/v1/workspaces",
        json={"slug": slug, "name": slug},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_project(client, csrf: str, workspace_id: str, slug: str) -> str:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={"slug": slug, "name": slug},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _publish_schema(client, csrf: str, workspace_id: str, project_id: str) -> None:
    headers = {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id}
    definition = {
        "label": "core",
        "description": "",
        "entity_extensions": [],
        "predicate_extensions": [],
    }
    draft = client.post(
        f"/api/v1/projects/{project_id}/schemas/drafts",
        json={"definition": definition},
        headers=headers,
    )
    assert draft.status_code == 201, draft.text
    version_id = draft.json()["version"]["id"]
    published = client.post(
        f"/api/v1/projects/{project_id}/schemas/{version_id}/publish", headers=headers
    )
    assert published.status_code == 200, published.text


def _create_canon_entity(client, csrf: str, workspace_id: str, project_id: str, name: str) -> dict:
    headers = {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id}
    response = client.post(
        f"/api/v1/projects/{project_id}/entities",
        json={"type": "Character", "name": name, "attributes": {}, "expected_revision": 0},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_hosted_graph_requires_workspace_header(hosted_client, hosted_container) -> None:
    _hosted_login(hosted_client, hosted_container)

    response = hosted_client.get("/api/v1/entities")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_hosted_graph_requires_project_header(hosted_client, hosted_container) -> None:
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "scope-hdr")

    response = hosted_client.get("/api/v1/entities", headers={"X-Workspace-Id": workspace_id})

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_hosted_graph_rejects_non_member_workspace(hosted_client, hosted_container) -> None:
    _hosted_login(hosted_client, hosted_container)

    response = hosted_client.get(
        "/api/v1/entities",
        headers={"X-Workspace-Id": "ws_other", "X-Project-Id": "prj_other"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_hosted_rejects_project_from_another_workspace(hosted_client, hosted_container) -> None:
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "scope-a")
    other_workspace = _create_workspace(hosted_client, csrf, "scope-b")
    other_project = _create_project(hosted_client, csrf, other_workspace, "proj-b")

    # The actor is a member of scope-a but the project belongs to scope-b.
    response = hosted_client.get(
        "/api/v1/entities",
        headers={"X-Workspace-Id": workspace_id, "X-Project-Id": other_project},
    )

    assert response.status_code == 404


def test_hosted_member_scopes_requests_to_their_workspace_and_project(
    hosted_client, hosted_container
) -> None:
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "scope-ws")
    project_id = _create_project(hosted_client, csrf, workspace_id, "scope-prj")
    _publish_schema(hosted_client, csrf, workspace_id, project_id)

    created = _create_canon_entity(hosted_client, csrf, workspace_id, project_id, "Scoped")
    entity_id = created["id"]

    headers = {"X-Workspace-Id": workspace_id}
    listed = hosted_client.get(f"/api/v1/projects/{project_id}/entities", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    other_project = _create_project(hosted_client, csrf, workspace_id, "scope-prj-2")
    other_listed = hosted_client.get(f"/api/v1/projects/{other_project}/entities", headers=headers)
    assert other_listed.status_code == 200
    assert other_listed.json()["items"] == []
    cross = hosted_client.get(
        f"/api/v1/projects/{other_project}/entities/{entity_id}", headers=headers
    )
    assert cross.status_code == 404


def test_hosted_generic_graph_writes_are_disabled(hosted_client, hosted_container) -> None:
    """The generic graph write path must not bypass the journal in hosted mode."""
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "generic")
    project_id = _create_project(hosted_client, csrf, workspace_id, "proj")
    headers = {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id, "X-Project-Id": project_id}

    entity = hosted_client.post(
        "/api/v1/entities",
        json={"id": "char_x", "type": "Character", "properties": {"name": "X"}},
        headers=headers,
    )
    assert entity.status_code == 403
    assert entity.json()["code"] == "forbidden"

    edge = hosted_client.post(
        "/api/v1/edges",
        json={"source": "a", "target": "b", "type": "KNOWS", "properties": {}},
        headers=headers,
    )
    assert edge.status_code == 403


def test_hosted_canon_entities_are_project_isolated(hosted_client, hosted_container) -> None:
    """The same name/type in two projects of one workspace must not interfere."""
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "same-id")
    project_a = _create_project(hosted_client, csrf, workspace_id, "alpha")
    project_b = _create_project(hosted_client, csrf, workspace_id, "beta")
    _publish_schema(hosted_client, csrf, workspace_id, project_a)
    _publish_schema(hosted_client, csrf, workspace_id, project_b)

    first = _create_canon_entity(hosted_client, csrf, workspace_id, project_a, "Alice A")
    second = _create_canon_entity(hosted_client, csrf, workspace_id, project_b, "Alice B")
    assert first["id"] != second["id"]

    headers = {"X-Workspace-Id": workspace_id}
    read_a = hosted_client.get(
        f"/api/v1/projects/{project_a}/entities/{first['id']}", headers=headers
    )
    read_b = hosted_client.get(
        f"/api/v1/projects/{project_b}/entities/{second['id']}", headers=headers
    )
    assert read_a.status_code == 200, read_a.text
    assert read_b.status_code == 200, read_b.text
    assert read_a.json()["name"] == "Alice A"
    assert read_b.json()["name"] == "Alice B"
    # Cross-project read of the other project's entity is not found.
    assert (
        hosted_client.get(
            f"/api/v1/projects/{project_b}/entities/{first['id']}", headers=headers
        ).status_code
        == 404
    )


def test_hosted_generate_questions_requires_credential(hosted_client, hosted_container) -> None:
    """Hosted workspaces have no platform key: BYOK is required for inference."""
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "no-cred")
    project_id = _create_project(hosted_client, csrf, workspace_id, "novel")

    response = hosted_client.post(
        f"/api/v1/graph/projects/{project_id}/characters/generate-questions",
        json={"id": "char_arin"},
        headers={"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "capability_unavailable"
