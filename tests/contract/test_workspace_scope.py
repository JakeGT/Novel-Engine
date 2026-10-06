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


def test_hosted_graph_requires_workspace_header(hosted_client, hosted_container) -> None:
    _hosted_login(hosted_client, hosted_container)

    response = hosted_client.get("/api/v1/entities")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_hosted_graph_rejects_non_member_workspace(hosted_client, hosted_container) -> None:
    _hosted_login(hosted_client, hosted_container)

    response = hosted_client.get("/api/v1/entities", headers={"X-Workspace-Id": "ws_other"})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_hosted_member_scopes_requests_to_their_workspace(hosted_client, hosted_container) -> None:
    csrf = _hosted_login(hosted_client, hosted_container)
    created = hosted_client.post(
        "/api/v1/workspaces",
        json={"slug": "scope-ws", "name": "Scope"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    workspace_id = created.json()["id"]

    listed = hosted_client.get("/api/v1/entities", headers={"X-Workspace-Id": workspace_id})
    assert listed.status_code == 200
    assert listed.json()["total"] == 0

    node = hosted_client.post(
        "/api/v1/entities",
        json={"id": "char_scoped", "type": "Character", "properties": {"name": "Scoped"}},
        headers={"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id},
    )
    assert node.status_code == 201

    # A second workspace the actor also owns cannot see the first workspace's nodes.
    second = hosted_client.post(
        "/api/v1/workspaces",
        json={"slug": "scope-ws-2", "name": "Scope 2"},
        headers={"X-CSRF-Token": csrf},
    )
    second_id = second.json()["id"]
    other_listing = hosted_client.get("/api/v1/entities", headers={"X-Workspace-Id": second_id})
    assert other_listing.status_code == 200
    assert other_listing.json()["total"] == 0
    assert (
        hosted_client.get(
            "/api/v1/entities/char_scoped", headers={"X-Workspace-Id": second_id}
        ).status_code
        == 404
    )
