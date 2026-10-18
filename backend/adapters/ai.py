from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from backend.core.logging import log_event
from backend.domain.providers import StructuredMode, request_model, spec_for_base_url
from backend.ports import (
    AIInvalidOutputError,
    AIProviderError,
    AIUsage,
    ProviderCapabilities,
    StructuredResponse,
    TextResponse,
)

logger = logging.getLogger(__name__)

MAX_LISTED_MODELS = 1000


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _field(container: Any, name: str) -> Any:
    if container is None:
        return None
    if isinstance(container, dict):
        return container.get(name)
    return getattr(container, name, None)


def _reported(usage: Any, name: str) -> Any:
    """A usage field, whether the client typed it or kept it among its extras."""
    found = _field(usage, name)
    if found is None:
        found = _field(_field(usage, "model_extra"), name)
    return found


def _cost(value: Any) -> float | None:
    """A reported cost as a finite, non-negative number of dollars, or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        found = float(value)
    except (TypeError, ValueError):
        return None
    return found if math.isfinite(found) and found >= 0 else None


def usage_from(usage: Any) -> AIUsage:
    """Token counts from a provider's ``usage``, including prompt tokens read from its cache.

    OpenAI and OpenRouter (for every provider it routes to, Anthropic included) report
    cache reads as ``prompt_tokens_details.cached_tokens``; DeepSeek as
    ``prompt_cache_hit_tokens``: both are part of ``prompt_tokens``. Anthropic's own shape
    (``input_tokens`` and ``output_tokens``) counts the tokens read from the cache
    (``cache_read_input_tokens``) and written to it (``cache_creation_input_tokens``)
    apart from ``input_tokens``, so the prompt is all three. Cached tokens never count
    for more than the prompt.

    OpenRouter also reports what the call cost, in credits worth a US dollar each, as
    ``usage.cost`` on every response; it is kept when present and never guessed."""
    if _reported(usage, "prompt_tokens") is None and _reported(usage, "input_tokens") is not None:
        cached = _count(_reported(usage, "cache_read_input_tokens"))
        prompt = (
            _count(_reported(usage, "input_tokens"))
            + cached
            + _count(_reported(usage, "cache_creation_input_tokens"))
        )
        completion = _count(_reported(usage, "output_tokens"))
    else:
        prompt = _count(_reported(usage, "prompt_tokens"))
        completion = _count(_reported(usage, "completion_tokens"))
        cached = _count(_field(_reported(usage, "prompt_tokens_details"), "cached_tokens"))
        if not cached:
            cached = _count(
                _reported(usage, "prompt_cache_hit_tokens")
                or _reported(usage, "cache_read_input_tokens")
            )
    return AIUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_prompt_tokens=min(cached, prompt),
        cost_usd=_cost(_reported(usage, "cost")),
    )


def prompted_messages(
    messages: list[dict[str, str]], response_format: type
) -> list[dict[str, str]]:
    """The ``reply-as-json`` prompt: the messages with the required shape asked for in words,
    for a model that cannot be held to a shape by the provider."""
    schema = json.dumps(response_format.model_json_schema())  # type: ignore[attr-defined]
    instruction = (
        "\n\nReply with one JSON object and nothing else: no explanation and no code "
        f"fence. It must be valid against this JSON Schema:\n{schema}"
    )
    shaped = [dict(message) for message in messages]
    if shaped and shaped[0].get("role") == "system":
        shaped[0]["content"] = f"{shaped[0]['content']}{instruction}"
    else:
        shaped.insert(0, {"role": "system", "content": instruction.strip()})
    return shaped


class OpenAIGateway:
    """AsyncOpenAI-compatible adapter.

    The platform client is configured from settings (``OPENAI_API_KEY``,
    ``AI_BASE_URL``, ``AI_PROVIDER``) for local/self-hosted or platform-funded
    use. A per-call ``api_key``/``base_url`` overrides it for BYOK workloads, so a
    hosted deployment never needs a platform key to run user-funded inference, and
    the provider is selected by base URL (``backend.domain.providers``).

    The answer must have a required shape. Where the provider enforces a schema it
    is asked to; where it does not, or refuses for the chosen model, the shape is
    described in the prompt and the answer is validated here.
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o",
        *,
        provider: str = "openai",
        base_url: str = "",
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._provider = provider
        self._base_url = base_url
        self._client: Any = None

    def _build_client(self, api_key: str | None, base_url: str | None) -> Any:
        from openai import AsyncOpenAI

        resolved_base_url = base_url or self._base_url
        kwargs: dict[str, Any] = {"api_key": api_key or self._api_key or "not-needed"}
        if resolved_base_url:
            kwargs["base_url"] = resolved_base_url
        return AsyncOpenAI(**kwargs)

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self._api_key and not self._base_url:
                raise AIProviderError("No AI provider credential or base URL is configured")
            self._client = self._build_client(self._api_key, self._base_url)
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key or self._base_url)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self._provider,
            model=self._default_model,
            input_modalities=frozenset({"text"}),
            output_modalities=frozenset({"text"}),
            structured_output=True,
            max_context_tokens=None,
            supports_streaming=False,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: type,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> StructuredResponse:
        from openai import OpenAIError

        # A workspace's own choice of model wins over the configured default.
        resolved_model = request_model() or model or self._default_model
        spec = spec_for_base_url(base_url or self._base_url)
        prompted = spec is not None and spec.structured is StructuredMode.PROMPTED
        start = time.perf_counter()
        per_call_client: Any = None
        if api_key or base_url:
            # BYOK path: never touch the platform client (which may be unconfigured).
            per_call_client = self._build_client(api_key, base_url)
            client = per_call_client
        else:
            client = self.client
        try:
            if prompted:
                parsed, response = await self._prompted(
                    client, resolved_model, messages, response_format
                )
            else:
                from openai import BadRequestError

                try:
                    response = await client.beta.chat.completions.parse(
                        model=resolved_model,
                        messages=messages,
                        response_format=response_format,
                    )
                    parsed = response.choices[0].message.parsed
                except BadRequestError:
                    # Refused as asked: commonly a model that cannot enforce a schema.
                    # A wrong model name fails again below and is reported as usual.
                    log_event(
                        logger,
                        "ai.generate.schema_refused",
                        level=logging.WARNING,
                        model=resolved_model,
                    )
                    parsed, response = await self._prompted(
                        client, resolved_model, messages, response_format
                    )
        except OpenAIError as error:
            log_event(
                logger,
                "ai.generate.error",
                level=logging.WARNING,
                model=resolved_model,
                duration_ms=round((time.perf_counter() - start) * 1000, 3),
                error_type=type(error).__name__,
            )
            # Provider text can contain request/account details; keep it as the
            # cause for logs but do not surface it through the API contract.
            raise AIProviderError("model provider request failed") from error
        finally:
            if per_call_client is not None:
                await per_call_client.close()

        if parsed is None:
            raise AIInvalidOutputError("provider returned no structured output")

        usage_model = usage_from(getattr(response, "usage", None))
        log_event(
            logger,
            "ai.generate",
            model=resolved_model,
            duration_ms=round((time.perf_counter() - start) * 1000, 3),
            prompt_tokens=usage_model.prompt_tokens,
            completion_tokens=usage_model.completion_tokens,
            cached_prompt_tokens=usage_model.cached_prompt_tokens,
        )
        return StructuredResponse(
            parsed=parsed,
            model=resolved_model,
            usage=usage_model,
        )

    async def generate_text(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> TextResponse:
        from openai import OpenAIError

        resolved_model = request_model() or model or self._default_model
        start = time.perf_counter()
        per_call_client: Any = None
        if api_key or base_url:
            per_call_client = self._build_client(api_key, base_url)
            client = per_call_client
        else:
            client = self.client
        try:
            response = await client.chat.completions.create(
                model=resolved_model, messages=messages, max_tokens=max_tokens
            )
        except OpenAIError as error:
            log_event(
                logger,
                "ai.generate_text.error",
                level=logging.WARNING,
                model=resolved_model,
                duration_ms=round((time.perf_counter() - start) * 1000, 3),
                error_type=type(error).__name__,
            )
            raise AIProviderError("model provider request failed") from error
        finally:
            if per_call_client is not None:
                await per_call_client.close()
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice is not None else ""
        if not text.strip():
            raise AIInvalidOutputError("provider returned no text")
        usage_model = usage_from(getattr(response, "usage", None))
        # Sizes and timing only: the text is the author's story.
        log_event(
            logger,
            "ai.generate_text",
            model=resolved_model,
            duration_ms=round((time.perf_counter() - start) * 1000, 3),
            prompt_tokens=usage_model.prompt_tokens,
            completion_tokens=usage_model.completion_tokens,
            cached_prompt_tokens=usage_model.cached_prompt_tokens,
        )
        return TextResponse(
            text=text,
            model=resolved_model,
            usage=usage_model,
            truncated=getattr(choice, "finish_reason", None) == "length",
        )

    @staticmethod
    async def _prompted(
        client: Any, model: str, messages: list[dict[str, str]], response_format: type
    ) -> tuple[Any, Any]:
        """Ask for JSON of the required shape in words, then validate what comes back."""
        shaped = prompted_messages(messages, response_format)
        response = await client.chat.completions.create(model=model, messages=shaped)
        text = (response.choices[0].message.content or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise AIInvalidOutputError("provider did not return a JSON object")
        try:
            parsed = response_format.model_validate_json(text[start : end + 1])  # type: ignore[attr-defined]
        except ValueError as error:
            # The text is the model's writing about the author's story: not logged, not surfaced.
            raise AIInvalidOutputError("provider returned JSON of the wrong shape") from error
        return parsed, response

    async def list_models(self, *, api_key: str, base_url: str) -> list[str]:
        """The model names this key can use, as the provider reports them."""
        from openai import OpenAIError

        client = self._build_client(api_key, base_url)
        try:
            page = await client.models.list()
            names = sorted({str(item.id) for item in page.data if getattr(item, "id", None)})
        except OpenAIError as error:
            log_event(
                logger,
                "ai.models.error",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )
            raise AIProviderError("model provider request failed") from error
        finally:
            await client.close()
        return names[:MAX_LISTED_MODELS]
