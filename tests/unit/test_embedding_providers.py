"""The embedding providers table: every entry well-formed, and its default sizes."""

from __future__ import annotations

import pytest

from backend.domain.embedding_providers import (
    EMBEDDING_PROVIDERS,
    MAX_DIMENSION,
    VOYAGE_DIMENSION_CHOICES,
    default_dimension,
    dimension_choices,
    embedding_provider,
)

pytestmark = pytest.mark.unit


def test_every_provider_is_well_formed() -> None:
    assert len(EMBEDDING_PROVIDERS) == 6
    ids = [spec.id for spec in EMBEDDING_PROVIDERS]
    assert ids == ["openai", "google", "mistral", "voyage", "openrouter", "custom"]
    for spec in EMBEDDING_PROVIDERS:
        assert spec.id and spec.label
        assert spec.group in {"Model makers", "Hosting and routing", "Your own"}
        if spec.base_url is not None:
            assert spec.base_url.startswith("https://")
        assert spec.docs.startswith("https://")
        assert spec.dimension_field in {"dimensions", "output_dimension"}
        for model in spec.models:
            assert model.id and model.dimension >= 1
        if spec.id != "custom":
            assert spec.key_url
            assert spec.models, f"{spec.id} should suggest at least one model"
        else:
            assert spec.base_url is None and spec.key_url is None


def test_provider_lookup() -> None:
    assert embedding_provider("openai").label == "OpenAI"  # type: ignore[union-attr]
    assert embedding_provider("voyage").query_field == ("input_type", "query", "document")  # type: ignore[union-attr]
    assert embedding_provider("custom").needs_key is False  # type: ignore[union-attr]
    assert embedding_provider("missing") is None


def test_dimension_choices_are_carried_on_the_provider_entry() -> None:
    voyage = embedding_provider("voyage")
    assert voyage is not None
    assert voyage.dimension_choices == (256, 512, 1024)
    assert dimension_choices("voyage") == list(voyage.dimension_choices)
    openai = embedding_provider("openai")
    assert openai is not None and openai.dimension_choices is None
    assert dimension_choices("missing") is None


def test_default_sizes_follow_the_table() -> None:
    assert default_dimension("text-embedding-3-small") == 1536
    # Larger than the index can hold and adjustable: asked for a shorter size.
    assert default_dimension("text-embedding-3-large") == 1536
    assert default_dimension("gemini-embedding-001") == 1536
    assert default_dimension("mistral-embed") == 1024
    assert default_dimension("codestral-embed") == 1536
    assert default_dimension("voyage-4") == 1024
    assert default_dimension("voyage-4-lite") == 1024
    assert default_dimension("voyage-4-large") == 1024
    assert default_dimension("openai/text-embedding-3-small") == 1536
    # A model not in the table has no default: the size must be given.
    assert default_dimension("something-else") is None


def test_voyage_only_offers_sizes_it_accepts_up_to_2000() -> None:
    assert dimension_choices("voyage") == [256, 512, 1024]
    assert dimension_choices("openai") is None
    # 2048 is accepted by Voyage but above the index's ceiling, so it is never offered.
    assert all(size <= MAX_DIMENSION for size in VOYAGE_DIMENSION_CHOICES)
