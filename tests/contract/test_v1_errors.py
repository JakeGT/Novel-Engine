import pytest

pytestmark = pytest.mark.contract


def test_rag_query_is_explicitly_not_implemented(api_client) -> None:
    response = api_client.post("/api/v1/rag/query", json={"query": "hello"})

    assert response.status_code == 501
    body = response.json()
    assert body["code"] == "not_implemented"
    assert body["correlation_id"]


def test_request_validation_uses_problem_contract(api_client) -> None:
    response = api_client.post("/api/v1/entities", json={"type": "Character"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert "id" in body["field_errors"]
    assert body["correlation_id"]


def test_correlation_id_is_echoed(api_client) -> None:
    response = api_client.get("/api/v1/entities/missing", headers={"X-Correlation-ID": "cid-123"})

    assert response.headers["X-Correlation-ID"] == "cid-123"
    assert response.json()["correlation_id"] == "cid-123"


def test_unknown_route_uses_problem_contract(api_client) -> None:
    response = api_client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json()["code"] == "http_404"


def test_ai_failure_maps_to_provider_failure(api_client, fake_container) -> None:
    from backend.ports import AIProviderError

    api_client.post("/api/v1/characters/init", json={"name": "Arin", "bio": "A scout."})
    fake_container.ai.queue(AIProviderError("upstream unavailable"))

    response = api_client.post("/api/v1/characters/generate-questions", json={"id": "char_arin"})

    assert response.status_code == 502
    assert response.json()["code"] == "provider_failure"
