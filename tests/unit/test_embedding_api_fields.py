"""The adapter's provider-specific request fields and its setup check."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.adapters.embedding_api import ApiTextEmbedding
from backend.domain.embedding import EmbeddingTask, TextInput

pytestmark = pytest.mark.unit


def _adapter(**overrides: object) -> ApiTextEmbedding:
    options: dict[str, object] = {
        "base_url": "https://embed.example.com/v1",
        "api_key": "sk-test-not-real",
        "model": "text-embedding-3-small",
        "dimension": 4,
    }
    options.update(overrides)
    return ApiTextEmbedding(**options)  # type: ignore[arg-type]


def _transport() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        body = json.loads(request.content)
        data = [
            {"index": index, "embedding": [float(index + 1)] * 4}
            for index, _ in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(handler), sent


def test_a_provider_that_wants_output_dimension_uses_it() -> None:
    transport, sent = _transport()
    embedder = _adapter(dimension_field="output_dimension", transport=transport)
    embedder.embed([TextInput(text="a")], task=EmbeddingTask.DOCUMENT)
    assert "dimensions" not in json.loads(sent[0].content)
    assert json.loads(sent[0].content)["output_dimension"] == 4


def test_a_provider_that_tells_query_from_document_sends_input_type() -> None:
    transport, sent = _transport()
    embedder = _adapter(query_field=("input_type", "query", "document"), transport=transport)
    embedder.embed([TextInput(text="a")], task=EmbeddingTask.QUERY)
    embedder.embed([TextInput(text="a")], task=EmbeddingTask.DOCUMENT)
    assert json.loads(sent[0].content)["input_type"] == "query"
    assert json.loads(sent[1].content)["input_type"] == "document"


def _check_transport(status: int, length: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "refused"}})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * length}]})

    return httpx.MockTransport(handler)


def test_the_check_reports_acceptance_refusal_and_size() -> None:
    ok = _adapter(transport=_check_transport(200, 4)).check("lighthouse")
    assert ok.ok and ok.status is None and ok.length is None

    refused = _adapter(transport=_check_transport(401, 4)).check("lighthouse")
    assert not refused.ok and refused.status == 401

    wrong = _adapter(transport=_check_transport(200, 3)).check("lighthouse")
    assert not wrong.ok and wrong.length == 3


def test_the_check_reports_an_unreachable_service() -> None:
    embedder = _adapter(
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("x"))
        )
    )
    result = embedder.check("lighthouse")
    assert not result.ok and result.failure == "ConnectError" and result.status is None


def test_a_2xx_answer_that_is_not_an_embeddings_response_is_unreadable() -> None:
    html = _adapter(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>"))
    )
    result = html.check("lighthouse")
    assert not result.ok and result.status == 200 and result.unreadable is True

    empty = _adapter(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []}))
    )
    empty_result = empty.check("lighthouse")
    assert not empty_result.ok and empty_result.status == 200 and empty_result.unreadable is True

    refused = _adapter(transport=_check_transport(400, 4)).check("lighthouse")
    assert refused.unreadable is False
