import types

import pytest
from openai import OpenAIError
from pydantic import BaseModel

from backend.adapters.ai import OpenAIGateway
from backend.ports import AIProviderError
from tests.doubles import ScriptedAIClient

pytestmark = pytest.mark.unit


class _Schema(BaseModel):
    value: str


class _FakeParseClient:
    def __init__(self, holder: dict) -> None:
        self._holder = holder

        async def _parse(**kwargs: object) -> object:
            holder["parse"] = kwargs
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(message=types.SimpleNamespace(parsed=_Schema(value="ok")))
                ],
                usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

        self.beta = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(parse=_parse))
        )

    async def close(self) -> None:
        self._holder["closed"] = True


@pytest.mark.asyncio
async def test_byok_call_uses_supplied_key_and_base_url_without_platform_key(
    monkeypatch,
) -> None:
    holder: dict = {}
    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")  # no platform key

    def _build(api_key: str | None, base_url: str | None) -> _FakeParseClient:
        holder["api_key"] = api_key
        holder["base_url"] = base_url
        return _FakeParseClient(holder)

    monkeypatch.setattr(gateway, "_build_client", _build)

    response = await gateway.generate_structured(
        messages=[{"role": "user", "content": "hi"}],
        response_format=_Schema,
        api_key="sk-byok-1234567890",
        base_url="https://openrouter.ai/api/v1",
    )

    assert response.parsed.value == "ok"
    assert holder["api_key"] == "sk-byok-1234567890"
    assert holder["base_url"] == "https://openrouter.ai/api/v1"
    assert holder["closed"] is True


def test_capabilities_report_configured_provider_and_availability() -> None:
    gateway = OpenAIGateway(
        api_key="",
        default_model="llama3",
        provider="ollama",
        base_url="http://localhost:11434/v1",
    )
    assert gateway.is_available() is True
    capabilities = gateway.capabilities()
    assert capabilities.provider == "ollama"
    assert capabilities.model == "llama3"


def test_no_platform_key_or_base_url_is_unavailable_and_raises() -> None:
    gateway = OpenAIGateway(api_key="")
    assert gateway.is_available() is False
    with pytest.raises(AIProviderError):
        _ = gateway.client


@pytest.mark.asyncio
async def test_provider_error_detail_is_not_leaked() -> None:
    gateway = OpenAIGateway(api_key="sk-test", default_model="gpt-4o")
    gateway._client = ScriptedAIClient(
        [OpenAIError("upstream detail sk-abcdefghijklmnop account=42")]
    )

    with pytest.raises(AIProviderError) as excinfo:
        await gateway.generate_structured(messages=[], response_format=_Schema)

    message = str(excinfo.value)
    assert message == "model provider request failed"
    assert "upstream detail" not in message
    assert "sk-" not in message
