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


def test_the_ai_route_says_local_provider_or_none_and_never_the_url(settings_factory) -> None:
    from backend.devtools.diagnostics import ai_route

    local = ai_route(settings_factory(OPENAI_API_KEY="", AI_BASE_URL="http://localhost:11434/v1"))
    assert local["kind"] == "local" and local["host"] == "localhost"
    remote = ai_route(
        settings_factory(OPENAI_API_KEY="", AI_BASE_URL="https://user:pw@openrouter.ai/api/v1?t=x")
    )
    assert remote["kind"] == "provider" and remote["host"] == "openrouter.ai"
    assert "pw" not in json.dumps(remote)
    assert ai_route(settings_factory(OPENAI_API_KEY="", AI_BASE_URL=""))["kind"] == "none"


@pytest.mark.asyncio
async def test_checking_the_ai_route_reports_each_question_by_kind_only(settings_factory) -> None:
    from backend.devtools.diagnostics import check_ai
    from backend.ports import AIProviderError
    from tests.doubles import ScriptedAIGateway

    settings = settings_factory(OPENAI_API_KEY="", AI_BASE_URL="http://localhost:1234/v1")
    working = await check_ai(settings, ScriptedAIGateway(["ready", object()]))
    assert working["ok"] is True and working["route"]["kind"] == "local"
    broken = await check_ai(
        settings, ScriptedAIGateway([AIProviderError("secret detail"), object()])
    )
    assert broken["ok"] is False and broken["prose"] == {"ok": False, "error": "AIProviderError"}
    assert "secret detail" not in json.dumps(broken)
    none = await check_ai(settings_factory(OPENAI_API_KEY="", AI_BASE_URL=""))
    assert none["ok"] is False and "AI_BASE_URL" in none["problem"]
