import types
from typing import Any

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


# ------------------------------------------------------------ the key check


def _model_list_client(status: int = 200, *, error: BaseException | None = None) -> Any:
    """An OpenAI client over a scripted transport, as the key check builds it."""
    import httpx
    from openai import AsyncOpenAI

    def handler(request: httpx.Request) -> httpx.Response:
        if error is not None:
            raise error
        return httpx.Response(status, json={"object": "list", "data": [{"id": "model-a"}]})

    return AsyncOpenAI(
        api_key="sk-test-key-123456",
        base_url="https://api.openai.com/v1",
        max_retries=0,
        timeout=10.0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _path_client(status: int = 200, *, error: BaseException | None = None) -> Any:
    """An httpx client over a scripted transport, for OpenRouter's ``GET /key``."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if error is not None:
            raise error
        return httpx.Response(status, json={"data": {"label": "key"}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, "ok"), (404, "unknown"), (429, "unknown"), (500, "unknown")],
)
async def test_check_key_via_the_models_list(monkeypatch, status: int, expected: str) -> None:
    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")
    monkeypatch.setattr(gateway, "_key_check_client", lambda key, url: _model_list_client(status))

    result = await gateway.check_key(
        api_key="sk-test-key-123456", base_url="https://api.openai.com/v1"
    )

    assert result == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_check_key_refuses_on_401_and_403(monkeypatch, status: int) -> None:
    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")
    monkeypatch.setattr(gateway, "_key_check_client", lambda key, url: _model_list_client(status))

    result = await gateway.check_key(
        api_key="sk-test-key-123456", base_url="https://api.openai.com/v1"
    )

    assert result == "refused"


@pytest.mark.asyncio
async def test_check_key_timeout_is_unknown(monkeypatch) -> None:
    import httpx

    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")
    monkeypatch.setattr(
        gateway,
        "_key_check_client",
        lambda key, url: _model_list_client(error=httpx.ReadTimeout("timed out")),
    )

    result = await gateway.check_key(
        api_key="sk-test-key-123456", base_url="https://api.openai.com/v1"
    )

    assert result == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, "ok"),
        (401, "refused"),
        (403, "refused"),
        (404, "unknown"),
        (429, "unknown"),
        (500, "unknown"),
    ],
)
async def test_check_key_via_openrouters_key_path(monkeypatch, status: int, expected: str) -> None:
    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")
    monkeypatch.setattr(gateway, "_check_http_client", lambda: _path_client(status))

    result = await gateway.check_key(
        api_key="sk-or-v1-test-key", base_url="https://openrouter.ai/api/v1"
    )

    assert result == expected


@pytest.mark.asyncio
async def test_check_key_openrouter_hits_the_key_path_with_the_key(monkeypatch) -> None:
    import httpx

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": {}})

    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")
    monkeypatch.setattr(
        gateway,
        "_check_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await gateway.check_key(
        api_key="sk-or-v1-test-key", base_url="https://openrouter.ai/api/v1"
    )

    assert result == "ok"
    assert seen["url"] == "https://openrouter.ai/api/v1/key"
    assert seen["authorization"] == "Bearer sk-or-v1-test-key"


@pytest.mark.asyncio
async def test_check_key_openrouter_timeout_is_unknown(monkeypatch) -> None:
    import httpx

    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")
    monkeypatch.setattr(
        gateway,
        "_check_http_client",
        lambda: _path_client(error=httpx.ReadTimeout("timed out")),
    )

    result = await gateway.check_key(
        api_key="sk-or-v1-test-key", base_url="https://openrouter.ai/api/v1"
    )

    assert result == "unknown"


@pytest.mark.asyncio
async def test_check_key_logs_provider_and_outcome_only(monkeypatch, caplog) -> None:
    import logging

    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")
    monkeypatch.setattr(gateway, "_key_check_client", lambda key, url: _model_list_client(200))

    with caplog.at_level(logging.INFO, logger="backend.adapters.ai"):
        result = await gateway.check_key(
            api_key="sk-test-key-123456", base_url="https://api.openai.com/v1"
        )

    assert result == "ok"
    events = [
        record for record in caplog.records if getattr(record, "event", None) == "ai.key_check"
    ]
    assert len(events) == 1
    assert getattr(events[0], "fields", None) == {"provider": "openai", "outcome": "ok"}


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


# --------------------------------------------------------- prompt tokens from the cache


def test_cached_prompt_tokens_are_read_from_the_openai_usage_shape() -> None:
    from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

    from backend.adapters.ai import usage_from

    usage = usage_from(
        CompletionUsage(
            prompt_tokens=1200,
            completion_tokens=300,
            total_tokens=1500,
            prompt_tokens_details=PromptTokensDetails(cached_tokens=1024),
        )
    )
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cached_prompt_tokens) == (
        1200,
        300,
        1024,
    )


@pytest.mark.parametrize(
    ("raw", "cached"),
    [
        # OpenRouter answers in the OpenAI shape, as a plain mapping here.
        ({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": 512}}, 512),
        # DeepSeek names its cache hits on the usage itself.
        ({"prompt_tokens": 900, "prompt_cache_hit_tokens": 640}, 640),
        # Anthropic's own shape.
        ({"prompt_tokens": 900, "cache_read_input_tokens": 300}, 300),
        # A provider that says nothing, or nonsense, counts as nothing from the cache.
        ({"prompt_tokens": 900}, 0),
        ({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": None}}, 0),
        ({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": -4}}, 0),
        # Never more than the prompt itself, even when the prompt is reported as nothing.
        ({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 512}}, 100),
        ({"prompt_tokens": 0, "prompt_tokens_details": {"cached_tokens": 512}}, 0),
        ({"prompt_cache_hit_tokens": 640}, 0),
    ],
)
def test_cached_prompt_tokens_from_other_providers(raw: dict, cached: int) -> None:
    from backend.adapters.ai import usage_from

    assert usage_from(raw).cached_prompt_tokens == cached


def test_anthropics_own_shape_counts_cache_reads_and_writes_in_the_prompt() -> None:
    from backend.adapters.ai import usage_from

    usage = usage_from(
        {
            "input_tokens": 50,
            "output_tokens": 200,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 300,
        }
    )
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cached_prompt_tokens) == (
        1350,
        200,
        1000,
    )
    # Without the cache fields it is simply its input and output.
    plain = usage_from({"input_tokens": 40, "output_tokens": 7})
    assert (plain.prompt_tokens, plain.completion_tokens, plain.cached_prompt_tokens) == (40, 7, 0)


def test_cache_reads_kept_among_a_clients_extras_are_counted() -> None:
    from openai.types.completion_usage import CompletionUsage

    from backend.adapters.ai import usage_from

    raw = CompletionUsage.model_validate(
        {
            "prompt_tokens": 900,
            "completion_tokens": 10,
            "total_tokens": 910,
            "prompt_cache_hit_tokens": 640,
        }
    )
    assert usage_from(raw).cached_prompt_tokens == 640


def test_no_usage_reported_is_all_zero() -> None:
    from backend.adapters.ai import usage_from

    usage = usage_from(None)
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cached_prompt_tokens) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_structured_answer_carries_its_cached_tokens(monkeypatch) -> None:
    gateway = OpenAIGateway(api_key="sk-platform", default_model="gpt-4o")

    async def _parse(**kwargs: object) -> object:
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(message=types.SimpleNamespace(parsed=_Schema(value="ok")))
            ],
            usage=types.SimpleNamespace(
                prompt_tokens=2048,
                completion_tokens=10,
                prompt_tokens_details=types.SimpleNamespace(cached_tokens=1536),
            ),
        )

    client = types.SimpleNamespace(
        beta=types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(parse=_parse))
        )
    )
    monkeypatch.setattr(gateway, "_client", client)
    response = await gateway.generate_structured(
        messages=[{"role": "user", "content": "hi"}], response_format=_Schema
    )
    assert response.usage.cached_prompt_tokens == 1536


@pytest.mark.asyncio
async def test_check_key_anthropic_uses_its_own_headers(monkeypatch) -> None:
    import httpx

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["x-api-key"] = request.headers.get("x-api-key", "")
        seen["version"] = request.headers.get("anthropic-version", "")
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(401 if seen["x-api-key"] == "sk-ant-wrong" else 200, json={})

    gateway = OpenAIGateway(api_key="", default_model="gpt-4o")
    monkeypatch.setattr(
        gateway,
        "_check_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert (
        await gateway.check_key(api_key="sk-ant-good-key", base_url="https://api.anthropic.com/v1/")
        == "ok"
    )
    assert seen["url"] == "https://api.anthropic.com/v1/models?limit=1"
    assert seen["x-api-key"] == "sk-ant-good-key"
    assert seen["version"] == "2023-06-01"
    assert seen["authorization"] == ""
    assert (
        await gateway.check_key(api_key="sk-ant-wrong", base_url="https://api.anthropic.com/v1/")
        == "refused"
    )
