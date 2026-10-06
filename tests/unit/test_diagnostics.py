import json

import pytest

from backend.devtools.diagnostics import build_diagnostics

pytestmark = pytest.mark.unit


def test_diagnostics_excludes_secrets(settings_factory) -> None:
    settings = settings_factory(
        OPENAI_API_KEY="sk-abcdefghijklmnopqrstuvwx",
        SENTRY_DSN_BACKEND="https://key@sentry.example/1",
        CREDENTIAL_KEK="super-secret-kek-value",
        POSTGRES_PASSWORD="hunter2",
        NEO4J_PASSWORD="neo4jpass",
        NEO4J_URI="bolt://neo4j:neo4jpass@neo4j:7687",
    )

    payload = build_diagnostics(settings)
    rendered = json.dumps(payload)

    assert "sk-abcdefghijklmnopqrstuvwx" not in rendered
    assert "hunter2" not in rendered
    assert "neo4jpass" not in rendered
    assert "super-secret-kek-value" not in rendered
    assert payload["capabilities"]["ai_configured"] is True
    assert payload["capabilities"]["credentials_configured"] is True
    assert payload["application"]["project"] == settings.PROJECT_NAME
