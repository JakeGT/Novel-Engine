from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from backend.adapters.connector_transport import (
    PLACEHOLDER_BASE_URL,
    DeviceTransport,
    device_failure,
    http_impl,
)
from backend.core.logging import log_event
from backend.domain.connector import MY_COMPUTER, parse_device_address
from backend.domain.providers import StructuredMode, request_model, spec_for_base_url
from backend.ports import (
    AIInvalidOutputError,
    AIProviderError,
    AIUnsupportedInputError,
    AIUsage,
    ImageInput,
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


def check_images(images: Sequence[ImageInput], capabilities: ProviderCapabilities) -> None:
    """Refuse, before anything is sent, pictures the route cannot take (CTX-013)."""
    if not images:
        return
    if not capabilities.sees_images:
        raise AIUnsupportedInputError("the chosen model cannot be sent pictures")
    if len(images) > capabilities.max_images:
        raise AIUnsupportedInputError(
            f"the chosen model takes at most {capabilities.max_images} pictures at once"
        )
    for image in images:
        if capabilities.image_types and image.content_type not in capabilities.image_types:
            raise AIUnsupportedInputError("the chosen model cannot take this kind of picture")
        if capabilities.max_image_bytes and len(image.data) > capabilities.max_image_bytes:
            raise AIUnsupportedInputError("a picture is larger than the chosen model takes")


def with_images(messages: list[dict[str, str]], images: Sequence[ImageInput]) -> list[Any]:
    """The messages in the OpenAI-compatible shape, with the pictures after the last user
    message's words (text first, then pictures, as OpenRouter recommends). Each picture
    is an inline ``data:`` address: never a lasting public address."""
    if not images:
        return list(messages)
    shaped: list[Any] = [dict(message) for message in messages]
    last = max(
        (index for index, message in enumerate(shaped) if message.get("role") == "user"),
        default=None,
    )
    if last is None:
        shaped.append({"role": "user", "content": ""})
        last = len(shaped) - 1
    parts: list[dict[str, Any]] = [{"type": "text", "text": shaped[last]["content"]}]
    for image in images:
        encoded = base64.b64encode(image.data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.content_type};base64,{encoded}"},
            }
        )
    shaped[last] = {"role": "user", "content": parts}
    return shaped


def stream_usage_option(base_url: str | None) -> bool:
    """Whether to ask the provider to report usage at the end of a stream
    (``stream_options.include_usage``). OpenRouter reports it on every stream anyway;
    a provider that refuses the option is asked again without it (see ``stream_text``)."""
    spec = spec_for_base_url(base_url)
    return spec is None or spec.stream_usage


@dataclass
class StreamedText:
    text: str
    finish_reason: str | None
    usage: Any
    # False when the provider refused to stream and the answer came whole instead.
    streamed: bool = True


# While a stream is open and the model sends nothing, how often the caller is still told
# (an empty piece), so a stop the author asked for, or a lost job, is noticed (SCN-002).
TICK_SECONDS = 1.0


def _refused(error: Exception) -> str:
    """What a refusal says, to tell which part of the request it is about (never logged)."""
    return str(error).lower()


def _about_usage_option(said: str) -> bool:
    return any(word in said for word in ("stream_options", "include_usage", "stream options"))


async def stream_text(
    client: Any,
    *,
    model: str,
    messages: list[Any],
    max_tokens: int,
    on_text: Callable[[str], Awaitable[None]],
    usage_option: bool = True,
) -> StreamedText:
    """Ask for the answer as a stream and hand each piece on as it comes (SCN-002).

    The pieces are kept in a list and joined once, so a long answer costs time in
    proportion to its length. Every chunk, and every ``TICK_SECONDS`` without one, the
    caller is told even when no words came (``on_text("")``: the model is thinking, or
    silent), so a stop is noticed whatever the model is doing. Whatever ``on_text``
    raises (the author stopped the work) closes the stream at once: for a provider that
    supports it, that also stops the model writing and charging. An error the provider
    sends part way through (OpenRouter sends ``finish_reason: "error"``) fails the
    request; nothing written so far is returned.

    A refusal before anything is written is asked again only when it is about what was
    asked of the stream: the usage option (asked again without it), or streaming itself
    (asked once for the whole answer). Any other refusal is the caller's to report."""
    from openai import BadRequestError, UnprocessableEntityError

    request: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if usage_option:
        request["stream_options"] = {"include_usage": True}
    stream: Any = None
    while stream is None:
        try:
            stream = await client.chat.completions.create(**request)
        except (BadRequestError, UnprocessableEntityError) as error:
            said = _refused(error)
            if "stream_options" in request and _about_usage_option(said):
                # A provider that does not know the usage option: asked once more
                # without it; usage is then estimated.
                request.pop("stream_options")
            elif "stream" in said:
                return await _whole_answer(client, request, on_text)
            else:
                raise
    pieces: list[str] = []
    finish: str | None = None
    usage: Any = None
    async with stream:
        chunks = stream.__aiter__()
        waiting: asyncio.Future[Any] | None = None
        try:
            while True:
                if waiting is None:
                    waiting = asyncio.ensure_future(chunks.__anext__())
                arrived, _ = await asyncio.wait({waiting}, timeout=TICK_SECONDS)
                if not arrived:
                    # Nothing yet: the caller still checks for a stop (the same chunk is
                    # waited for again, never asked for twice).
                    await on_text("")
                    continue
                current, waiting = waiting, None
                try:
                    chunk = current.result()
                except StopAsyncIteration:
                    break
                reported = getattr(chunk, "usage", None)
                if reported is not None:
                    usage = reported
                wrote = False
                for choice in getattr(chunk, "choices", None) or ():
                    delta = getattr(choice, "delta", None)
                    piece = getattr(delta, "content", None) if delta is not None else None
                    if piece:
                        wrote = True
                        pieces.append(piece)
                        await on_text(piece)
                    if getattr(choice, "finish_reason", None):
                        finish = choice.finish_reason
                if not wrote:
                    # Thinking, a role, a usage report or a keep-alive: no words, but a tick.
                    await on_text("")
        finally:
            if waiting is not None:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
    if finish == "error":
        raise AIProviderError("model provider request failed")
    text = "".join(pieces)
    if not text.strip():
        raise AIInvalidOutputError("provider returned no text")
    return StreamedText(text=text, finish_reason=finish, usage=usage)


async def _whole_answer(
    client: Any, request: dict[str, Any], on_text: Callable[[str], Awaitable[None]]
) -> StreamedText:
    """The provider refused to stream: the answer asked for once, whole, and handed on as
    one piece."""
    whole = {
        key: value for key, value in request.items() if key not in ("stream", "stream_options")
    }
    response = await client.chat.completions.create(**whole)
    choice = response.choices[0] if response.choices else None
    text = (choice.message.content or "") if choice is not None else ""
    if not text.strip():
        raise AIInvalidOutputError("provider returned no text")
    await on_text(text)
    return StreamedText(
        text=text,
        finish_reason=getattr(choice, "finish_reason", None),
        usage=getattr(response, "usage", None),
        streamed=False,
    )


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
        image_input: bool = False,
        image_max_count: int = 4,
        image_max_bytes: int = 5 * 1024 * 1024,
        image_types: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/webp"}),
        image_providers: frozenset[str] = frozenset(),
        devices: Any = None,
        streaming: bool = True,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._provider = provider
        self._base_url = base_url
        self._client: Any = None
        # CTX-013: which routes can see pictures, as the operator declared them.
        self._image_input = image_input
        self._image_max_count = max(0, image_max_count)
        self._image_max_bytes = max(0, image_max_bytes)
        self._image_types = image_types
        self._image_providers = image_providers
        # A model on the author's own computer, reached through the connector (SCN-002A).
        self._devices = devices
        # SCN-002: whether drafts are asked for as a stream (``AI_STREAMING``).
        self._streaming = streaming
        # CTX-013: what each model can take, from its provider's list or the operator's
        # table; None negotiates per route, as before.
        self._models: Any = None

    def attach_devices(self, devices: Any) -> None:
        self._devices = devices

    def attach_models(self, models: Any) -> None:
        """What each model can take (``ModelCapabilityService``), per request (CTX-013)."""
        self._models = models

    def _build_client(self, api_key: str | None, base_url: str | None) -> Any:
        from openai import AsyncOpenAI

        device = parse_device_address(base_url)
        if device is not None:
            if self._devices is None:
                raise AIProviderError("Computers cannot be connected on this site.")
            workspace_id, device_id = device
            transport = DeviceTransport(
                self._devices.relay, workspace_id=workspace_id, device_id=device_id
            )
            # No retries: a computer that did not answer says why, at once.
            return AsyncOpenAI(
                api_key="connector",
                base_url=PLACEHOLDER_BASE_URL,
                max_retries=0,
                timeout=None,
                http_client=http_impl.AsyncClient(transport=transport, timeout=None),
            )
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

    def capabilities(
        self, *, base_url: str | None = None, model: str | None = None
    ) -> ProviderCapabilities:
        """The configured route, or a workspace's own provider at ``base_url``, for
        ``model`` (by default the model this request uses, or the configured one).

        A preview made outside a request names the workspace's chosen model here: what
        that model takes is what decides, never the operator's default (CTX-013).

        A workspace's provider can see pictures only when the operator listed it in
        ``AI_IMAGE_PROVIDERS``; otherwise, like an unconfigured route, it is text only."""
        chosen = model
        device = parse_device_address(base_url)
        if device is not None:
            known = self._devices.known_capabilities(device[1]) if self._devices else None
            model = chosen or request_model() or self._default_model
            # Text only: pictures are not sent to a computer (ADR-025), and its answer
            # comes whole through the relay, so it never streams (SCN-002).
            return ProviderCapabilities(
                provider=MY_COMPUTER,
                model=model,
                structured_output=bool(known and known.structured_output),
                max_context_tokens=known.context_for(model) if known else None,
            )
        provider = self._provider
        allowed = self._image_input
        if base_url and base_url.rstrip("/") != self._base_url.rstrip("/"):
            spec = spec_for_base_url(base_url)
            provider = spec.id if spec is not None else "unknown"
            allowed = spec is not None and spec.id in self._image_providers
        model = chosen or request_model() or self._default_model
        # CTX-013: what this model takes, by name (the operator's table over the
        # provider's list). The operator still decides whether pictures may go at all.
        listed = self._models.lookup(provider, model) if self._models is not None else None
        sees = allowed and (listed is None or listed.sees_images is not False)
        max_images = self._image_max_count
        if listed is not None and listed.max_images:
            max_images = min(max_images, listed.max_images)
        return ProviderCapabilities(
            provider=provider,
            model=model,
            input_modalities=frozenset({"text", "image"}) if sees else frozenset({"text"}),
            output_modalities=frozenset({"text"}),
            structured_output=listed is None or listed.structured is not False,
            max_context_tokens=listed.context_tokens if listed is not None else None,
            supports_streaming=self._streaming,
            max_images=max_images if sees else 0,
            max_image_bytes=self._image_max_bytes if sees else 0,
            image_types=self._image_types if sees else frozenset(),
            max_output_tokens=listed.max_output_tokens if listed is not None else None,
            source=listed.source if listed is not None else "route",
        )

    async def refresh_capabilities(
        self, *, base_url: str | None = None, model: str | None = None
    ) -> None:
        """Make what is known of the route's models fresh before a request (CTX-013).
        ``model`` is accepted for symmetry with ``capabilities``: a provider's list covers
        every model behind it."""
        del model
        if self._models is None or parse_device_address(base_url) is not None:
            return
        provider = self._provider
        if base_url and base_url.rstrip("/") != self._base_url.rstrip("/"):
            spec = spec_for_base_url(base_url)
            provider = spec.id if spec is not None else "unknown"
        await self._models.ensure(provider)

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
        images: Sequence[ImageInput] = (),
    ) -> StructuredResponse:
        from openai import OpenAIError

        # A workspace's own choice of model wins over the configured default.
        resolved_model = request_model() or model or self._default_model
        spec = spec_for_base_url(base_url or self._base_url)
        prompted = spec is not None and spec.structured is StructuredMode.PROMPTED
        await self.refresh_capabilities(base_url=base_url, model=resolved_model)
        capabilities = self.capabilities(base_url=base_url, model=resolved_model)
        if not capabilities.structured_output:
            # A computer whose model server, or a model whose provider's list says it
            # does not keep to a schema, is asked in words.
            prompted = True
        # A picture the route cannot take is refused here, before any client is built.
        check_images(images, capabilities)
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
                    client, resolved_model, messages, response_format, images
                )
            else:
                from openai import BadRequestError

                try:
                    response = await client.beta.chat.completions.parse(
                        model=resolved_model,
                        messages=with_images(messages, images),
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
                        client, resolved_model, messages, response_format, images
                    )
        except (OpenAIError, http_impl.TransportError) as error:
            log_event(
                logger,
                "ai.generate.error",
                level=logging.WARNING,
                model=resolved_model,
                duration_ms=round((time.perf_counter() - start) * 1000, 3),
                error_type=type(error).__name__,
            )
            if (failure := device_failure(error)) is not None:
                raise failure from None
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
            # How many pictures went with it; never the pictures.
            images=len(images),
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
        images: Sequence[ImageInput] = (),
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> TextResponse:
        from openai import OpenAIError

        resolved_model = request_model() or model or self._default_model
        await self.refresh_capabilities(base_url=base_url, model=resolved_model)
        capabilities = self.capabilities(base_url=base_url, model=resolved_model)
        # A picture the route cannot take is refused here, before any client is built.
        check_images(images, capabilities)
        start = time.perf_counter()
        per_call_client: Any = None
        if api_key or base_url:
            per_call_client = self._build_client(api_key, base_url)
            client = per_call_client
        else:
            client = self.client
        try:
            if on_text is not None and capabilities.supports_streaming:
                # SCN-002: the answer in pieces as it is written; the whole of it, and
                # what it used, still come back once at the end.
                streamed = await stream_text(
                    client,
                    model=resolved_model,
                    messages=with_images(messages, images),
                    max_tokens=max_tokens,
                    on_text=on_text,
                    usage_option=stream_usage_option(base_url or self._base_url),
                )
                usage_model = usage_from(streamed.usage)
                log_event(
                    logger,
                    "ai.generate_text",
                    model=resolved_model,
                    duration_ms=round((time.perf_counter() - start) * 1000, 3),
                    prompt_tokens=usage_model.prompt_tokens,
                    completion_tokens=usage_model.completion_tokens,
                    cached_prompt_tokens=usage_model.cached_prompt_tokens,
                    images=len(images),
                    streamed=streamed.streamed,
                    usage_reported=streamed.usage is not None,
                )
                return TextResponse(
                    text=streamed.text,
                    model=resolved_model,
                    usage=usage_model,
                    truncated=streamed.finish_reason == "length",
                    usage_reported=streamed.usage is not None,
                    streamed=streamed.streamed,
                )
            response = await client.chat.completions.create(
                model=resolved_model,
                messages=with_images(messages, images),
                max_tokens=max_tokens,
            )
        except (OpenAIError, http_impl.TransportError) as error:
            # A connection broken part way through a stream is raised by the HTTP library
            # itself, not wrapped by the client: it is a provider failure all the same.
            log_event(
                logger,
                "ai.generate_text.error",
                level=logging.WARNING,
                model=resolved_model,
                duration_ms=round((time.perf_counter() - start) * 1000, 3),
                error_type=type(error).__name__,
            )
            if (failure := device_failure(error)) is not None:
                raise failure from None
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
            images=len(images),
        )
        return TextResponse(
            text=text,
            model=resolved_model,
            usage=usage_model,
            truncated=getattr(choice, "finish_reason", None) == "length",
        )

    @staticmethod
    async def _prompted(
        client: Any,
        model: str,
        messages: list[dict[str, str]],
        response_format: type,
        images: Sequence[ImageInput] = (),
    ) -> tuple[Any, Any]:
        """Ask for JSON of the required shape in words, then validate what comes back."""
        shaped = with_images(prompted_messages(messages, response_format), images)
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
