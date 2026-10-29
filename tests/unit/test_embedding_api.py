"""The embedding service adapter (EMBEDDING_PROVIDER=api), against a fake HTTP service."""

from __future__ import annotations

import json
import logging
import math
from types import SimpleNamespace

import httpx
import pytest

from backend.adapters.embedding import build_multimodal_embedder
from backend.adapters.embedding_api import MAX_INPUTS_PER_REQUEST, ApiTextEmbedding
from backend.core.config import Settings
from backend.domain.embedding import (
    EmbeddingError,
    EmbeddingTask,
    EmbeddingUnavailableError,
    ImageInput,
    TextInput,
    UnsupportedInputError,
)

SECRET_WORDS = "Mara buried the ledger under the third lamp"


class Service:
    """A fake embeddings endpoint that records what it was sent."""

    def __init__(self, *, dimension: int = 4, statuses: list[int] | None = None) -> None:
        self.dimension = dimension
        self.statuses = list(statuses or [])
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.statuses:
            status = self.statuses.pop(0)
            if status != 200:
                # A real service may quote what it was sent in its error.
                body = {"error": {"message": f"bad input: {SECRET_WORDS}"}}
                headers = {"Retry-After": "3"} if status == 429 else {}
                return httpx.Response(status, json=body, headers=headers)
        sent = json.loads(request.content)
        data = [
            {"index": index, "embedding": [float(index + 1)] * self.dimension}
            for index, _ in enumerate(sent["input"])
        ]
        # Answer out of order: the adapter must put them back by index.
        return httpx.Response(200, json={"data": list(reversed(data))})


def adapter(service: Service, **overrides: object) -> tuple[ApiTextEmbedding, list[float]]:
    waits: list[float] = []
    options: dict[str, object] = {
        "base_url": "https://embed.example.com/v1",
        "api_key": "sk-test-not-real",
        "model": "text-embedding-3-small",
        "dimension": 4,
        "transport": httpx.MockTransport(service),
        "sleep": waits.append,
    }
    options.update(overrides)
    return ApiTextEmbedding(**options), waits  # type: ignore[arg-type]


def test_health_says_what_is_missing_without_calling_the_service() -> None:
    service = Service()
    no_key, _ = adapter(service, api_key="")
    plain_http, _ = adapter(service, base_url="http://embed.example.com/v1")
    no_model, _ = adapter(service, model="")
    assert "EMBEDDING_API_KEY" in (no_key.health().reason or "")
    assert "https" in (plain_http.health().reason or "")
    assert "EMBEDDING_API_MODEL" in (no_model.health().reason or "")
    ready, _ = adapter(service)
    assert ready.health().available and not ready.health().loaded
    assert service.requests == []


def test_a_model_on_this_machine_needs_no_key_and_keeps_words_on_the_server() -> None:
    local, _ = adapter(Service(), base_url="http://localhost:11434/v1", api_key="")
    assert local.available()
    assert local.where_words_go().kind == "server"
    remote, _ = adapter(Service(), name="OpenAI")
    where = remote.where_words_go()
    assert (where.kind, where.name, where.host) == ("service", "OpenAI", "embed.example.com")


def test_the_service_is_part_of_the_space() -> None:
    one, _ = adapter(Service())
    other, _ = adapter(Service(), base_url="https://other.example.org/v1")
    assert one.space.key != other.space.key
    assert one.space.revision == "api:embed.example.com"


def test_texts_are_sent_in_groups_and_come_back_in_order_and_normalized() -> None:
    service = Service()
    embedder, _ = adapter(service)
    inputs = [TextInput(text=f"passage {n}") for n in range(MAX_INPUTS_PER_REQUEST + 3)]
    vectors = embedder.embed(inputs, task=EmbeddingTask.DOCUMENT)
    assert len(service.requests) == 2
    first = json.loads(service.requests[0].content)
    assert first["model"] == "text-embedding-3-small"
    assert first["dimensions"] == 4
    assert first["encoding_format"] == "float"
    assert len(first["input"]) == MAX_INPUTS_PER_REQUEST
    assert service.requests[0].headers["Authorization"] == "Bearer sk-test-not-real"
    assert len(vectors) == len(inputs)
    for vector in vectors:
        assert math.isclose(math.fsum(v * v for v in vector.values), 1.0)
        assert vector.provenance.space_key == embedder.space.key
    assert vectors[0].provenance.content_hash == inputs[0].content_hash
    assert embedder.loaded()


def test_the_size_is_not_sent_when_the_service_refuses_it() -> None:
    service = Service()
    embedder, _ = adapter(service, send_dimension=False)
    embedder.embed([TextInput(text="a")], task=EmbeddingTask.QUERY)
    assert "dimensions" not in json.loads(service.requests[0].content)


def test_a_wrong_size_is_refused_with_the_setting_to_check() -> None:
    embedder, _ = adapter(Service(dimension=3))
    with pytest.raises(EmbeddingError, match="EMBEDDING_API_DIMENSION"):
        embedder.embed([TextInput(text="a")], task=EmbeddingTask.DOCUMENT)


def test_a_refused_key_is_not_tried_again_and_the_reply_is_never_quoted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = Service(statuses=[401])
    embedder, waits = adapter(service)
    with caplog.at_level(logging.DEBUG), pytest.raises(EmbeddingUnavailableError) as raised:
        embedder.embed([TextInput(text=SECRET_WORDS)], task=EmbeddingTask.DOCUMENT)
    assert "refused the key" in str(raised.value)
    assert SECRET_WORDS not in str(raised.value)
    assert len(service.requests) == 1 and waits == []
    assert SECRET_WORDS not in caplog.text
    assert "sk-test-not-real" not in caplog.text


def test_a_busy_service_is_tried_again_after_the_wait_it_asks_for() -> None:
    service = Service(statuses=[429, 200])
    embedder, waits = adapter(service)
    vectors = embedder.embed([TextInput(text="a")], task=EmbeddingTask.DOCUMENT)
    assert len(vectors) == 1
    assert waits == [3.0]


def test_a_failing_service_gives_up_after_a_few_tries(caplog: pytest.LogCaptureFixture) -> None:
    service = Service(statuses=[500, 503, 502])
    embedder, waits = adapter(service)
    with caplog.at_level(logging.DEBUG), pytest.raises(EmbeddingUnavailableError):
        embedder.embed([TextInput(text=SECRET_WORDS)], task=EmbeddingTask.DOCUMENT)
    assert len(service.requests) == 3
    assert waits == [1.0, 2.0]
    assert SECRET_WORDS not in caplog.text


def test_pictures_are_refused_rather_than_dropped() -> None:
    embedder, _ = adapter(Service())
    with pytest.raises(UnsupportedInputError):
        embedder.embed([ImageInput(data=b"x", media_type="image/png")], task=EmbeddingTask.DOCUMENT)


def test_the_builder_makes_the_service_adapter_from_settings() -> None:
    settings = SimpleNamespace(
        EMBEDDING_PROVIDER="api",
        EMBEDDING_API_BASE_URL="https://api.openai.com/v1",
        EMBEDDING_API_KEY="",
        EMBEDDING_API_MODEL="text-embedding-3-small",
        EMBEDDING_API_DIMENSION=768,
        EMBEDDING_API_SEND_DIMENSION=True,
        EMBEDDING_API_NAME="OpenAI",
        EMBEDDING_API_TIMEOUT_SECONDS=30.0,
    )
    embedder = build_multimodal_embedder(settings, legacy=None)  # type: ignore[arg-type]
    assert isinstance(embedder, ApiTextEmbedding)
    assert embedder.space.dimension == 768
    assert not embedder.available()  # no key yet


def test_a_size_the_search_index_cannot_hold_is_refused_at_start_up() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_API_DIMENSION"):
        Settings(EMBEDDING_PROVIDER="api", EMBEDDING_API_DIMENSION=3072, _env_file=None)


def test_a_query_is_tried_once_and_quickly_since_someone_is_waiting() -> None:
    service = Service(statuses=[503, 200])
    embedder, waits = adapter(service, timeout_seconds=30.0)
    with pytest.raises(EmbeddingUnavailableError):
        embedder.embed([TextInput(text="lantern")], task=EmbeddingTask.QUERY)
    assert len(service.requests) == 1 and waits == []
    assert service.requests[0].extensions["timeout"]["read"] == 8.0
