import json
from pathlib import Path

import pytest

from backend.main import create_app

pytestmark = pytest.mark.contract

SNAPSHOT = Path(__file__).parent / "openapi_snapshot.json"

REQUIRED_PATHS = {
    "/api/v1/entities",
    "/api/v1/entities/{entity_id}",
    "/api/v1/entities/search",
    "/api/v1/edges",
    "/api/v1/schema/node-types",
    "/api/v1/context",
    "/api/v1/rag/query",
    "/api/v1/characters/init",
    "/api/v1/characters/generate-questions",
    "/api/v1/characters/process-answers",
    "/health/live",
    "/health/ready",
    "/health/capabilities",
}


def test_openapi_generates_without_services() -> None:
    spec = create_app().openapi()

    assert REQUIRED_PATHS.issubset(set(spec["paths"]))


def test_openapi_matches_snapshot() -> None:
    spec = create_app().openapi()
    assert SNAPSHOT.exists(), f"missing snapshot; regenerate {SNAPSHOT.name}"

    assert spec == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


SITE_SEARCH_RESPONSES = {
    ("/api/v1/site/search-providers", "get"): "SearchProviderModel",
    ("/api/v1/site/search-setup", "get"): "SiteSearchStateModel",
    ("/api/v1/site/search-setup", "put"): "SaveSearchSetupModel",
    ("/api/v1/site/search-setup", "delete"): "SiteSearchStateModel",
    ("/api/v1/site/search-setup/build", "post"): "SiteSearchStateModel",
    ("/api/v1/site/search-setup/build/cancel", "post"): "SiteSearchStateModel",
}

SITE_SEARCH_SCHEMAS = {
    "BuiltInModel",
    "SaveSearchSetupModel",
    "SearchBuildModel",
    "SearchIndexModel",
    "SearchModelModel",
    "SearchProviderModel",
    "SearchSetupDetailModel",
    "SearchSetupKeyModel",
    "SiteSearchStateModel",
}


def _response_schema(spec: dict, path: str, method: str) -> dict:
    schema = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    # A list route wraps its item model in an array schema.
    return schema["items"] if schema.get("type") == "array" else schema


def test_site_search_routes_name_their_response_models() -> None:
    spec = create_app().openapi()

    for (path, method), model in SITE_SEARCH_RESPONSES.items():
        assert _response_schema(spec, path, method)["$ref"] == f"#/components/schemas/{model}"

    assert SITE_SEARCH_SCHEMAS <= set(spec["components"]["schemas"])
