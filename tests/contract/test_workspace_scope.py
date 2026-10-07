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
    headers = {"X-Workspace-Id": workspace_id, "X-Project-Id": project_id}

    listed = hosted_client.get("/api/v1/entities", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 0

    node = hosted_client.post(
        "/api/v1/entities",
        json={"id": "char_scoped", "type": "Character", "properties": {"name": "Scoped"}},
        headers={**headers, "X-CSRF-Token": csrf},
    )
    assert node.status_code == 201

    # Canonical project-prefixed path resolves the same project.
    canonical = hosted_client.get(f"/api/v1/projects/{project_id}/entities", headers=headers)
    assert canonical.status_code == 200
    assert canonical.json()["total"] == 1

    # A second project in the same workspace cannot see the first project's nodes.
    other_project = _create_project(hosted_client, csrf, workspace_id, "scope-prj-2")
    other_headers = {"X-Workspace-Id": workspace_id, "X-Project-Id": other_project}
    other_listing = hosted_client.get("/api/v1/entities", headers=other_headers)
    assert other_listing.status_code == 200
    assert other_listing.json()["total"] == 0
    assert (
        hosted_client.get("/api/v1/entities/char_scoped", headers=other_headers).status_code == 404
    )


def test_hosted_cannot_span_projects_with_an_edge(hosted_client, hosted_container) -> None:
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "scope-edges")
    project_a = _create_project(hosted_client, csrf, workspace_id, "proj-a")
    project_b = _create_project(hosted_client, csrf, workspace_id, "proj-b")
    headers_a = {
        "X-Workspace-Id": workspace_id,
        "X-Project-Id": project_a,
        "X-CSRF-Token": csrf,
    }
    headers_b = {
        "X-Workspace-Id": workspace_id,
        "X-Project-Id": project_b,
        "X-CSRF-Token": csrf,
    }

    hosted_client.post(
        "/api/v1/entities",
        json={"id": "char_a", "type": "Character", "properties": {"name": "A"}},
        headers=headers_a,
    )
    hosted_client.post(
        "/api/v1/entities",
        json={"id": "char_b", "type": "Character", "properties": {"name": "B"}},
        headers=headers_b,
    )

    # The target lives in a sibling project of the same workspace: not visible.
    edge = hosted_client.post(
        "/api/v1/edges",
        json={"source": "char_a", "target": "char_b", "type": "KNOWS", "properties": {}},
        headers=headers_a,
    )
    assert edge.status_code == 400


def test_hosted_same_id_in_two_projects_coexists(hosted_client, hosted_container) -> None:
    """The same entity id in two projects of one workspace must not interfere."""
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "same-id")
    project_a = _create_project(hosted_client, csrf, workspace_id, "alpha")
    project_b = _create_project(hosted_client, csrf, workspace_id, "beta")
    write_headers = {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id}

    first = hosted_client.post(
        f"/api/v1/projects/{project_a}/entities",
        json={"id": "char_alice", "type": "Character", "properties": {"name": "Alice A"}},
        headers=write_headers,
    )
    assert first.status_code == 201, first.text
    second = hosted_client.post(
        f"/api/v1/projects/{project_b}/entities",
        json={"id": "char_alice", "type": "Character", "properties": {"name": "Alice B"}},
        headers=write_headers,
    )
    assert second.status_code == 201, second.text

    read_a = hosted_client.get(
        f"/api/v1/projects/{project_a}/entities/char_alice",
        headers={"X-Workspace-Id": workspace_id},
    )
    read_b = hosted_client.get(
        f"/api/v1/projects/{project_b}/entities/char_alice",
        headers={"X-Workspace-Id": workspace_id},
    )
    assert read_a.status_code == 200, read_a.text
    assert read_b.status_code == 200, read_b.text
    assert read_a.json()["properties"]["name"] == "Alice A"
    assert read_b.json()["properties"]["name"] == "Alice B"


def test_hosted_generate_questions_requires_credential(hosted_client, hosted_container) -> None:
    """Hosted workspaces have no platform key: BYOK is required for inference."""
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "no-cred")
    project_id = _create_project(hosted_client, csrf, workspace_id, "novel")

    response = hosted_client.post(
        f"/api/v1/projects/{project_id}/characters/generate-questions",
        json={"id": "char_arin"},
        headers={"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "capability_unavailable"
