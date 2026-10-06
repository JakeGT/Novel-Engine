import pytest
from fastapi.testclient import TestClient

from backend.main import create_app

pytestmark = pytest.mark.contract


def test_character_init_endpoint_uses_injected_container(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        response = client.post("/api/characters/init", json={"name": "Arin"})

    assert response.status_code == 200
    assert response.json()["node"]["id"] == "char_arin"
    assert fake_container.graph.get_node("char_arin") is not None


def test_schema_endpoint_uses_injected_registry(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        response = client.post("/api/schema/nodes/", json={"type": "Starship"})
        listed = client.get("/api/schema/nodes/")

    assert response.status_code == 200
    assert "Starship" in response.json()["types"]
    assert "Starship" in listed.json()["types"]
    assert fake_container.schema_registry.is_valid_node_type("Starship")
