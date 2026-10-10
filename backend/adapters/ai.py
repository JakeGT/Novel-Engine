from __future__ import annotations

import json
import logging
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
)

logger = logging.getLogger(__name__)

MAX_LISTED_MODELS = 1000


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

        usage = getattr(response, "usage", None)
        usage_model = AIUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        log_event(
            logger,
            "ai.generate",
            model=resolved_model,
            duration_ms=round((time.perf_counter() - start) * 1000, 3),
            prompt_tokens=usage_model.prompt_tokens,
            completion_tokens=usage_model.completion_tokens,
        )
        return StructuredResponse(
            parsed=parsed,
            model=resolved_model,
            usage=usage_model,
        )

    @staticmethod
    async def _prompted(
        client: Any, model: str, messages: list[dict[str, str]], response_format: type
    ) -> tuple[Any, Any]:
        """Ask for JSON of the required shape in words, then validate what comes back."""
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
