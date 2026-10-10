"""The AI providers a workspace can connect with its own key.

Every provider here is reached through its OpenAI-compatible endpoint, so one
adapter serves them all. What differs is the address, how the key is obtained,
and how reliably the provider can be made to answer in a required shape:

- ``schema``: the provider enforces a JSON schema itself (strict structured output).
- ``prompted``: the shape is described in the prompt and the answer is validated
  here. Used where the provider's compatible endpoint ignores or lacks schemas.

A provider marked ``schema`` still falls back to ``prompted`` when it refuses a
schema for the chosen model, so a model without that feature remains usable.

Default models are suggestions only. Model names change often; the product reads
the live list from the provider with the workspace's key and lets the owner choose.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum


class StructuredMode(StrEnum):
    SCHEMA = "schema"
    PROMPTED = "prompted"


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    base_url: str
    structured: StructuredMode
    # Where an owner creates a key, shown beside the key field.
    key_url: str
    # A sensible starting model, or None when the owner must choose one.
    default_model: str | None
    note: str = ""


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        structured=StructuredMode.SCHEMA,
        key_url="https://openrouter.ai/keys",
        default_model="anthropic/claude-sonnet-5.5",
        note="One key for models from many companies.",
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic (Claude)",
        base_url="https://api.anthropic.com/v1/",
        structured=StructuredMode.PROMPTED,
        key_url="https://console.anthropic.com/settings/keys",
        default_model="claude-sonnet-5-5",
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        structured=StructuredMode.SCHEMA,
        key_url="https://platform.openai.com/api-keys",
        default_model=None,
    ),
    ProviderSpec(
        id="google",
        label="Google (Gemini)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        structured=StructuredMode.SCHEMA,
        key_url="https://aistudio.google.com/apikey",
        default_model=None,
    ),
    ProviderSpec(
        id="xai",
        label="xAI (Grok)",
        base_url="https://api.x.ai/v1",
        structured=StructuredMode.SCHEMA,
        key_url="https://console.x.ai",
        default_model=None,
    ),
    ProviderSpec(
        id="mistral",
        label="Mistral",
        base_url="https://api.mistral.ai/v1",
        structured=StructuredMode.SCHEMA,
        key_url="https://console.mistral.ai/api-keys",
        default_model="mistral-large-latest",
    ),
    ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        structured=StructuredMode.PROMPTED,
        key_url="https://platform.deepseek.com/api_keys",
        default_model="deepseek-chat",
    ),
)

_BY_ID = {spec.id: spec for spec in PROVIDERS}


def provider_spec(provider_id: str) -> ProviderSpec | None:
    return _BY_ID.get(provider_id)


def spec_for_base_url(base_url: str | None) -> ProviderSpec | None:
    if not base_url:
        return None
    wanted = base_url.rstrip("/")
    return next((spec for spec in PROVIDERS if spec.base_url.rstrip("/") == wanted), None)


# The model chosen for the workspace whose request is being served. Set once the
# provider route is resolved, read by the gateway; None means "use the configured
# default" (a self-hosted copy, where the operator sets the model).
_request_model: ContextVar[str | None] = ContextVar("ai_request_model", default=None)


def set_request_model(model: str | None) -> None:
    _request_model.set(model)


def request_model() -> str | None:
    return _request_model.get()
