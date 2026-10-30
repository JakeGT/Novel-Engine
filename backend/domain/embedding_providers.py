"""The embedding providers the site can be pointed at (its search vectors).

Each provider is one entry: where its service is, where a key is made, what a key looks
like, and a few suggested models. Adding a provider is one entry (and, for the page, one
docs row). Any model id can be typed; the suggestions are only a starting point.

Every provider here answers an OpenAI-compatible ``POST {base}/embeddings``, so one
adapter serves them all. What differs is the address, the request field the service
uses for a requested size, whether a query and a document are told apart, and whether a
key is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# The search index holds vectors of at most 2,000 numbers (the pgvector ``vector``
# type's HNSW ceiling), so no model is ever asked for more than this.
MAX_DIMENSION = 2000
# A size a model does not state but can be asked for (its name says it can shorten).
_DEFAULT_SHORT = 1536


@dataclass(frozen=True)
class EmbeddingModelSpec:
    id: str
    # What it gives by default.
    dimension: int
    # It can be asked for a shorter size (Matryoshka); then we ask for <= 2000.
    adjustable: bool


@dataclass(frozen=True)
class EmbeddingProviderSpec:
    id: str
    label: str
    group: Literal["Model makers", "Hosting and routing", "Your own"]
    # None: the person gives the address (custom).
    base_url: str | None
    docs: str
    # Where a key is created, and what one looks like.
    key_url: str | None
    key_hint: str | None
    # Suggestions; any model id can be typed.
    models: tuple[EmbeddingModelSpec, ...]
    # The request field the service reads for a requested size.
    dimension_field: Literal["dimensions", "output_dimension"]
    # ("input_type", "query", "document") for Voyage, which tells a query from a document.
    query_field: tuple[str, str, str] | None = None
    # Custom: only when the address is not local.
    needs_key: bool = True
    # The sizes the service accepts (<= 2000); None when any size works.
    dimension_choices: tuple[int, ...] | None = None


# Voyage only accepts these sizes; 2048 is above the index's 2000 ceiling, so it is
# never offered here.
VOYAGE_DIMENSION_CHOICES: tuple[int, ...] = (256, 512, 1024)

EMBEDDING_PROVIDERS: tuple[EmbeddingProviderSpec, ...] = (
    EmbeddingProviderSpec(
        id="openai",
        label="OpenAI",
        group="Model makers",
        base_url="https://api.openai.com/v1",
        docs="https://developers.openai.com/api/reference/resources/embeddings/methods/create",
        key_url="https://platform.openai.com/api-keys",
        key_hint="Starts with sk-",
        models=(
            EmbeddingModelSpec(id="text-embedding-3-small", dimension=1536, adjustable=True),
            EmbeddingModelSpec(id="text-embedding-3-large", dimension=3072, adjustable=True),
        ),
        dimension_field="dimensions",
    ),
    EmbeddingProviderSpec(
        id="google",
        label="Google Gemini",
        group="Model makers",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        docs="https://ai.google.dev/gemini-api/docs/openai",
        key_url="https://aistudio.google.com/apikey",
        key_hint="Starts with AIza",
        models=(EmbeddingModelSpec(id="gemini-embedding-001", dimension=3072, adjustable=True),),
        dimension_field="dimensions",
    ),
    EmbeddingProviderSpec(
        id="mistral",
        label="Mistral",
        group="Model makers",
        base_url="https://api.mistral.ai/v1",
        docs="https://docs.mistral.ai/capabilities/embeddings/",
        key_url="https://console.mistral.ai/api-keys",
        key_hint=None,
        models=(
            EmbeddingModelSpec(id="mistral-embed", dimension=1024, adjustable=False),
            EmbeddingModelSpec(id="codestral-embed", dimension=1536, adjustable=True),
        ),
        dimension_field="output_dimension",
    ),
    EmbeddingProviderSpec(
        id="voyage",
        label="Voyage AI",
        group="Model makers",
        base_url="https://api.voyageai.com/v1",
        docs="https://docs.voyageai.com/reference/embeddings-api",
        key_url="https://dashboard.voyageai.com/",
        key_hint="Starts with pa-",
        models=(
            EmbeddingModelSpec(id="voyage-4", dimension=1024, adjustable=True),
            EmbeddingModelSpec(id="voyage-4-lite", dimension=1024, adjustable=True),
            EmbeddingModelSpec(id="voyage-4-large", dimension=1024, adjustable=True),
        ),
        dimension_field="output_dimension",
        query_field=("input_type", "query", "document"),
        dimension_choices=VOYAGE_DIMENSION_CHOICES,
    ),
    EmbeddingProviderSpec(
        id="openrouter",
        label="OpenRouter",
        group="Hosting and routing",
        base_url="https://openrouter.ai/api/v1",
        docs="https://openrouter.ai/docs/api-reference/embeddings",
        key_url="https://openrouter.ai/keys",
        key_hint="Starts with sk-or-",
        models=(
            EmbeddingModelSpec(id="openai/text-embedding-3-small", dimension=1536, adjustable=True),
        ),
        dimension_field="dimensions",
    ),
    EmbeddingProviderSpec(
        id="custom",
        label="Your own (OpenAI-compatible)",
        group="Your own",
        base_url=None,
        docs="https://developers.openai.com/api/reference/resources/embeddings/methods/create",
        key_url=None,
        key_hint="Whatever your server expects; a local Ollama needs none",
        models=(),
        dimension_field="dimensions",
        needs_key=False,
    ),
)

_BY_ID = {spec.id: spec for spec in EMBEDDING_PROVIDERS}


def embedding_provider(provider_id: str) -> EmbeddingProviderSpec | None:
    return _BY_ID.get(provider_id)


def model_spec(model: str) -> EmbeddingModelSpec | None:
    """The first table entry for a model id, whichever provider suggests it."""
    for provider in EMBEDDING_PROVIDERS:
        for candidate in provider.models:
            if candidate.id == model:
                return candidate
    return None


def default_dimension(model: str) -> int | None:
    """The size a model gives by default, or None when the size must be given.

    A model the table knows gives its own size; one larger than the index can hold
    and that can be shortened is asked for 1536. A model not in the table has no
    default here, so the person must give a size.
    """
    spec = model_spec(model)
    if spec is None:
        return None
    if spec.dimension <= MAX_DIMENSION:
        return spec.dimension
    if spec.adjustable:
        return _DEFAULT_SHORT
    return None


def dimension_choices(provider_id: str) -> list[int] | None:
    """The sizes a provider's service accepts (<= 2000), or None when any size works."""
    spec = _BY_ID.get(provider_id)
    if spec is None or spec.dimension_choices is None:
        return None
    return list(spec.dimension_choices)


__all__ = [
    "EMBEDDING_PROVIDERS",
    "MAX_DIMENSION",
    "VOYAGE_DIMENSION_CHOICES",
    "EmbeddingModelSpec",
    "EmbeddingProviderSpec",
    "default_dimension",
    "dimension_choices",
    "embedding_provider",
    "model_spec",
]
