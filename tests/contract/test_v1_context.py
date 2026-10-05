import pytest

pytestmark = pytest.mark.contract


def _seed_character(api_client) -> None:
    api_client.post(
        "/api/v1/entities",
        json={"id": "char_arin", "type": "Character", "properties": {"name": "Arin"}},
    )


def test_context_with_explicit_anchor(api_client) -> None:
    _seed_character(api_client)

    response = api_client.post(
        "/api/v1/context",
        json={
            "text": "[Arin](entity:Character:char_arin) arrives at the gate.",
            "context_type": "scene_generation",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["policy_used"] == "scene_generation"
    assert any(node["id"] == "char_arin" for node in body["nodes"])
    assert "EXTERNAL KNOWLEDGE CONTEXT" in body["summary"]


def test_context_without_anchors_is_empty(api_client) -> None:
    response = api_client.post("/api/v1/context", json={"text": "no entity tags here"})

    assert response.status_code == 200
    assert response.json()["nodes"] == []
    assert response.json()["summary"] == ""
