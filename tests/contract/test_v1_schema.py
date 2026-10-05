import pytest

pytestmark = pytest.mark.contract


def test_node_type_lifecycle(api_client) -> None:
    listed = api_client.get("/api/v1/schema/node-types")
    assert listed.status_code == 200
    assert "Character" in listed.json()["types"]

    added = api_client.post("/api/v1/schema/node-types", json={"type": "Starship"})
    assert added.status_code == 201
    assert "Starship" in added.json()["types"]

    duplicate = api_client.post("/api/v1/schema/node-types", json={"type": "Starship"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"

    removed = api_client.delete("/api/v1/schema/node-types/Starship")
    assert removed.status_code == 200
    assert "Starship" not in removed.json()["types"]


def test_node_type_rejects_invalid_identifier(api_client) -> None:
    response = api_client.post("/api/v1/schema/node-types", json={"type": "bad type!"})

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_removing_core_or_missing_type_returns_not_found(api_client) -> None:
    response = api_client.delete("/api/v1/schema/node-types/Character")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_edge_type_lifecycle(api_client) -> None:
    added = api_client.post("/api/v1/schema/edge-types", json={"type": "PILOTS"})
    assert added.status_code == 201
    assert "PILOTS" in added.json()["types"]

    listed = api_client.get("/api/v1/schema/edge-types")
    assert "KNOWS" in listed.json()["types"]

    removed = api_client.delete("/api/v1/schema/edge-types/PILOTS")
    assert removed.status_code == 200
    assert "PILOTS" not in removed.json()["types"]


def test_type_listing_is_deterministic(api_client) -> None:
    first = api_client.get("/api/v1/schema/node-types").json()["types"]
    second = api_client.get("/api/v1/schema/node-types").json()["types"]

    assert first == second == sorted(first)
