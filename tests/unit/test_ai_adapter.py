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


class _Completions:
    """An SDK-shaped client whose strict and plain calls are scripted separately."""

    def __init__(self, holder: dict, *, parse: object = None, text: str = "") -> None:
        self._holder = holder

        async def _parse(**kwargs: object) -> object:
            holder.setdefault("parse", []).append(kwargs)
            if isinstance(parse, BaseException):
                raise parse
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(parsed=parse))],
                usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

        async def _create(**kwargs: object) -> object:
            holder.setdefault("create", []).append(kwargs)
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))],
                usage=types.SimpleNamespace(prompt_tokens=7, completion_tokens=5),
            )

        async def _list() -> object:
            holder["listed"] = True
            return types.SimpleNamespace(
                data=[types.SimpleNamespace(id=name) for name in ("zeta", "alpha", "alpha")]
            )

        self.beta = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(parse=_parse))
        )
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=_create))
        self.models = types.SimpleNamespace(list=_list)

    async def close(self) -> None:
        self._holder["closed"] = True


def _gateway(monkeypatch, holder: dict, **client: object) -> OpenAIGateway:
    gateway = OpenAIGateway(api_key="", default_model="default-model")
    monkeypatch.setattr(gateway, "_build_client", lambda key, url: _Completions(holder, **client))
    return gateway


@pytest.mark.asyncio
async def test_a_provider_without_schemas_is_asked_in_words_and_its_answer_validated(
    monkeypatch,
) -> None:
    holder: dict = {}
    gateway = _gateway(monkeypatch, holder, text='Here you go:\n```json\n{"value": "ok"}\n```')

    response = await gateway.generate_structured(
        messages=[{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}],
        response_format=_Schema,
        api_key="sk-ant-1234567890",
        base_url="https://api.anthropic.com/v1/",
    )

    assert response.parsed == _Schema(value="ok")
    assert response.usage.prompt_tokens == 7
    assert "parse" not in holder
    sent = holder["create"][0]
    assert "response_format" not in sent
    system = sent["messages"][0]["content"]
    assert system.startswith("Be brief.") and '"value"' in system and "JSON Schema" in system
    assert holder["closed"] is True


@pytest.mark.asyncio
async def test_an_answer_of_the_wrong_shape_is_refused_not_passed_on(monkeypatch) -> None:
    from backend.ports import AIInvalidOutputError

    for text in ('{"other": 1}', "I cannot help with that."):
        gateway = _gateway(monkeypatch, {}, text=text)
        with pytest.raises(AIInvalidOutputError):
            await gateway.generate_structured(
                messages=[{"role": "user", "content": "hi"}],
                response_format=_Schema,
                api_key="sk-1234567890",
                base_url="https://api.deepseek.com",
            )


@pytest.mark.asyncio
async def test_a_refused_schema_falls_back_to_asking_in_words(monkeypatch) -> None:
    import httpx
    from openai import BadRequestError

    refused = BadRequestError(
        "schema not supported",
        response=httpx.Response(400, request=httpx.Request("POST", "https://x.test")),
        body=None,
    )
    holder: dict = {}
    gateway = _gateway(monkeypatch, holder, parse=refused, text='{"value": "ok"}')

    response = await gateway.generate_structured(
        messages=[{"role": "user", "content": "hi"}],
        response_format=_Schema,
        api_key="sk-1234567890",
        base_url="https://openrouter.ai/api/v1",
    )

    assert response.parsed == _Schema(value="ok")
    assert len(holder["parse"]) == 1 and len(holder["create"]) == 1


@pytest.mark.asyncio
async def test_the_workspaces_chosen_model_wins_over_the_default(monkeypatch) -> None:
    from backend.domain.providers import set_request_model

    holder: dict = {}
    gateway = _gateway(monkeypatch, holder, parse=_Schema(value="ok"))
    set_request_model("chosen-model")
    try:
        response = await gateway.generate_structured(
            messages=[{"role": "user", "content": "hi"}],
            response_format=_Schema,
            model="workflow-default",
            api_key="sk-1234567890",
            base_url="https://api.openai.com/v1",
        )
    finally:
        set_request_model(None)

    assert holder["parse"][0]["model"] == "chosen-model" and response.model == "chosen-model"


@pytest.mark.asyncio
async def test_models_are_listed_from_the_provider_sorted_and_without_repeats(monkeypatch) -> None:
    holder: dict = {}
    gateway = _gateway(monkeypatch, holder)

    names = await gateway.list_models(api_key="sk-1234567890", base_url="https://api.x.ai/v1")

    assert names == ["alpha", "zeta"]
    assert holder["closed"] is True


def test_every_provider_has_a_distinct_address_and_a_key_page() -> None:
    from backend.domain.credentials import CredentialProvider
    from backend.domain.providers import PROVIDERS, provider_spec, spec_for_base_url

    assert {spec.id for spec in PROVIDERS} == {provider.value for provider in CredentialProvider}
    assert len({spec.base_url.rstrip("/") for spec in PROVIDERS}) == len(PROVIDERS)
    for spec in PROVIDERS:
        assert spec.base_url.startswith("https://") and spec.key_url.startswith("https://")
        assert spec_for_base_url(spec.base_url.rstrip("/")) is spec
        assert provider_spec(spec.id) is spec
    assert spec_for_base_url("http://localhost:11434/v1") is None
