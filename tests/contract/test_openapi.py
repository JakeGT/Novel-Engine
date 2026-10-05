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
