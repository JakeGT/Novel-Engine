from __future__ import annotations

import logging
import time
from typing import Any

from backend.core.logging import log_event
from backend.ports import (
    AIInvalidOutputError,
    AIProviderError,
    AIUsage,
    ProviderCapabilities,
    StructuredResponse,
)

logger = logging.getLogger(__name__)


class OpenAIGateway:
    """AsyncOpenAI adapter normalizing provider errors and usage."""

    def __init__(self, api_key: str, default_model: str = "gpt-4o") -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise AIProviderError("OpenAI API key is not configured")
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="openai",
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
    ) -> StructuredResponse:
        from openai import OpenAIError

        resolved_model = model or self._default_model
        start = time.perf_counter()
        try:
            response = await self.client.beta.chat.completions.parse(
                model=resolved_model,
                messages=messages,
                response_format=response_format,
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

        parsed = response.choices[0].message.parsed
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
