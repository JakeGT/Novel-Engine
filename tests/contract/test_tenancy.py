import asyncio

import pytest

from backend.domain.identity import Role

pytestmark = pytest.mark.contract


def _login(client) -> str:
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    return client.cookies.get("novel_csrf")


def test_tenancy_requires_session(api_client) -> None:
    assert api_client.get("/api/v1/workspaces").status_code == 401


def test_workspace_project_and_invitation_flow(api_client) -> None:
    csrf = _login(api_client)
    headers = {"X-CSRF-Token": csrf}

    created = api_client.post(
        "/api/v1/workspaces", json={"slug": "harbor", "name": "Harbor"}, headers=headers
    )
    assert created.status_code == 201
    workspace = created.json()

    # CSRF is required for mutations.
    assert api_client.post("/api/v1/workspaces", json={"slug": "x", "name": "X"}).status_code == 403

    listing = api_client.get("/api/v1/workspaces")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [workspace["id"]]

    detail = api_client.get(f"/api/v1/workspaces/{workspace['id']}")
    assert detail.status_code == 200 and detail.json()["slug"] == "harbor"

    members = api_client.get(f"/api/v1/workspaces/{workspace['id']}/members").json()
    assert [m["role"] for m in members] == [Role.OWNER.value]
    # Members are listed with a name to show; an owner also sees the sign-in address.
    assert "display_name" in members[0] and "email" in members[0]
    assert members[0]["display_name"]

    project = api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/projects",
        json={"slug": "novel", "name": "Novel"},
        headers=headers,
    )
    assert project.status_code == 201
    assert (
        api_client.get(f"/api/v1/workspaces/{workspace['id']}/projects").json()[0]["slug"]
        == "novel"
    )

    invitation = api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/invitations",
        json={"role": "editor"},
        headers=headers,
    )
    assert invitation.status_code == 201
    body = invitation.json()
    assert body["token"]
    invitation_id = body["invitation"]["id"]

    # The raw token is shown once and never listed again.
    listed = api_client.get(f"/api/v1/workspaces/{workspace['id']}/invitations").json()
    assert all("token" not in item for item in listed)

    revoked = api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/invitations/{invitation_id}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True


def test_invalid_slug_is_rejected(api_client) -> None:
    csrf = _login(api_client)
    response = api_client.post(
        "/api/v1/workspaces",
        json={"slug": "Not A Slug!", "name": "X"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_non_member_cannot_read_workspace(api_client, fake_container) -> None:
    _login(api_client)
    other = asyncio.run(fake_container.tenancy.create_workspace(slug="other", name="Other"))
    asyncio.run(
        fake_container.tenancy.add_membership(
            workspace_id=other.id, user_id="someone-else", role=Role.OWNER
        )
    )

    response = api_client.get(f"/api/v1/workspaces/{other.id}")
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"

    # unauthenticated listing does not reveal workspace existence
    api_client.cookies.clear()
    assert api_client.get("/api/v1/workspaces").status_code == 401


def test_delete_workspace_requires_csrf_and_removes_workspace(api_client) -> None:
    csrf = _login(api_client)
    created = api_client.post(
        "/api/v1/workspaces",
        json={"slug": "del-api", "name": "Delete API"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    workspace_id = created.json()["id"]

    assert api_client.delete(f"/api/v1/workspaces/{workspace_id}").status_code == 403
    deleted = api_client.delete(
        f"/api/v1/workspaces/{workspace_id}", headers={"X-CSRF-Token": csrf}
    )
    assert deleted.status_code == 204
    # After deletion the actor is no longer a member, so reads stay hidden (403), not 404.
    assert api_client.get(f"/api/v1/workspaces/{workspace_id}").status_code == 403
