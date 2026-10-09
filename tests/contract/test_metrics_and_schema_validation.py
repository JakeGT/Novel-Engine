"""Metrics endpoint access rules and project-aware schema validation."""

import pytest

from backend.core.metrics import metrics
from tests.contract.test_canon_entities import _project_id

pytestmark = pytest.mark.contract


def _client(settings_factory, tmp_path, **overrides):
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from tests.conftest import _build_fake_container

    container = _build_fake_container(settings_factory(**overrides), tmp_path)
    return TestClient(create_app(container=container))


def test_metrics_are_off_by_default(api_client) -> None:
    assert api_client.get("/health/metrics").status_code == 404


def test_metrics_require_the_token_when_one_is_set(settings_factory, tmp_path) -> None:
    metrics.reset()
    metrics.increment("canon_commits_total", "Commits.", origin="author", outcome="committed")
    with _client(
        settings_factory, tmp_path, APP_ENV="test", METRICS_ENABLED=True, METRICS_TOKEN="s3cret"
    ) as client:
        assert client.get("/health/metrics").status_code == 401
        wrong = client.get("/health/metrics", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        ok = client.get("/health/metrics", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        assert ok.headers["content-type"].startswith("text/plain")
        assert 'canon_commits_total{origin="author",outcome="committed"} 1' in ok.text
        # The scrape endpoint is operational, not part of the product API contract.
        assert "/health/metrics" not in client.get("/openapi.json").json()["paths"]
    metrics.reset()


def test_development_may_serve_metrics_without_a_token(settings_factory, tmp_path) -> None:
    with _client(settings_factory, tmp_path, APP_ENV="test", METRICS_ENABLED=True) as client:
        assert client.get("/health/metrics").status_code == 200


def test_production_never_serves_metrics_unauthenticated() -> None:
    """Decided by the handler itself, independent of the rest of production config."""
    from types import SimpleNamespace

    from starlette.requests import Request

    from backend.api.health import operational_metrics

    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    settings = SimpleNamespace(METRICS_ENABLED=True, METRICS_TOKEN="", APP_ENV="production")
    response = operational_metrics(request, SimpleNamespace(settings=settings))
    assert response.status_code == 403


SHIP = {
    "label": "ships",
    "entity_extensions": [
        {"entity_type": "Ship", "fields": [{"name": "tonnage", "field_type": "integer"}]}
    ],
}


def test_schema_validation_reports_errors_and_real_compatibility(
    auth_client, fake_container
) -> None:
    base = f"/api/v1/projects/{_project_id(fake_container)}/schemas"

    first = auth_client.post(f"{base}/validate", json={"definition": SHIP}).json()
    assert first["valid"] is True and first["compatibility"] == "additive"
    assert first["entity_types"] == ["Ship"]

    broken = auth_client.post(
        f"{base}/validate", json={"definition": {"label": "x", "entity_extensions": [{"nope": 1}]}}
    )
    assert broken.status_code == 200
    assert broken.json()["valid"] is False and broken.json()["compatibility"] == "invalid"
    assert broken.json()["errors"]

    draft = auth_client.post(f"{base}/drafts", json={"definition": SHIP}).json()["version"]
    assert auth_client.post(f"{base}/{draft['id']}/publish").status_code == 200

    # Against the published version, removing a type is no longer "additive".
    removed = auth_client.post(
        f"{base}/validate", json={"definition": {"label": "ships", "entity_extensions": []}}
    ).json()
    assert removed["valid"] is True
    assert removed["compatibility"] in {"migration_required", "forbidden"}
    assert removed["migration_plan"]

    grown = {
        **SHIP,
        "entity_extensions": [
            *SHIP["entity_extensions"],
            {"entity_type": "Port", "fields": []},
        ],
    }
    assert (
        auth_client.post(f"{base}/validate", json={"definition": grown}).json()["compatibility"]
        == "additive"
    )
