from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.contract


def test_schema_requires_authentication(api_client) -> None:
    assert api_client.get("/api/v1/schema/node-types").status_code == 401
    assert (
        api_client.post("/api/v1/schema/node-types", json={"type": "Starship"}).status_code == 401
    )
    assert api_client.get("/api/v1/schema/edge-types").status_code == 401


def test_node_type_lifecycle(auth_client) -> None:
    listed = auth_client.get("/api/v1/schema/node-types")
    assert listed.status_code == 200
    assert "Character" in listed.json()["types"]

    added = auth_client.post("/api/v1/schema/node-types", json={"type": "Starship"})
    assert added.status_code == 201
    assert "Starship" in added.json()["types"]

    duplicate = auth_client.post("/api/v1/schema/node-types", json={"type": "Starship"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"

    removed = auth_client.delete("/api/v1/schema/node-types/Starship")
    assert removed.status_code == 200
    assert "Starship" not in removed.json()["types"]


def test_node_type_rejects_invalid_identifier(auth_client) -> None:
    response = auth_client.post("/api/v1/schema/node-types", json={"type": "bad type!"})

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_removing_core_or_missing_type_returns_not_found(auth_client) -> None:
    response = auth_client.delete("/api/v1/schema/node-types/Character")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_edge_type_lifecycle(auth_client) -> None:
    added = auth_client.post("/api/v1/schema/edge-types", json={"type": "PILOTS"})
    assert added.status_code == 201
    assert "PILOTS" in added.json()["types"]

    listed = auth_client.get("/api/v1/schema/edge-types")
    assert "KNOWS" in listed.json()["types"]

    removed = auth_client.delete("/api/v1/schema/edge-types/PILOTS")
    assert removed.status_code == 200
    assert "PILOTS" not in removed.json()["types"]


def test_type_listing_is_deterministic(auth_client) -> None:
    first = auth_client.get("/api/v1/schema/node-types").json()["types"]
    second = auth_client.get("/api/v1/schema/node-types").json()["types"]

    assert first == second == sorted(first)


def test_hosted_platform_admin_required(hosted_client, hosted_container) -> None:
    # A regular hosted member (not in PLATFORM_ADMIN_USER_IDS) cannot mutate global schema.
    login = hosted_client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    hosted_client.get(f"/auth/callback?state={state}&code=code-123", follow_redirects=False)
    csrf = hosted_client.cookies.get(hosted_container.settings.CSRF_COOKIE_NAME)

    response = hosted_client.post(
        "/api/v1/schema/node-types",
        json={"type": "Starship"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
