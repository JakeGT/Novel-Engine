import pytest

from backend.adapters.ai import OpenAIGateway
from tests.doubles import ScriptedAIGateway

pytestmark = pytest.mark.unit


def test_openai_gateway_reports_text_only_capabilities() -> None:
    capabilities = OpenAIGateway(api_key="sk-test", default_model="gpt-4o").capabilities()

    assert capabilities.provider == "openai"
    assert capabilities.model == "gpt-4o"
    assert capabilities.input_modalities == frozenset({"text"})
    assert capabilities.output_modalities == frozenset({"text"})
    assert capabilities.structured_output is True
    # Drafts stream unless the operator turns it off (SCN-002, AI_STREAMING).
    assert capabilities.supports_streaming is True
    assert capabilities.source == "route"
    off = OpenAIGateway(api_key="sk-test", streaming=False).capabilities()
    assert off.supports_streaming is False


def test_scripted_gateway_reports_capabilities() -> None:
    capabilities = ScriptedAIGateway().capabilities()

    assert capabilities.provider == "scripted"
    assert capabilities.structured_output is True
    assert "text" in capabilities.input_modalities


def test_health_capabilities_include_provider_capabilities(api_client) -> None:
    response = api_client.get("/health/capabilities")

    assert response.status_code == 200
    ai = response.json()["capabilities"]["ai"]
    assert ai["capabilities"]["provider"] == "scripted"
    assert ai["capabilities"]["input_modalities"] == ["text"]
