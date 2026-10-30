"""Search by meaning through an embedding service (EMBEDDING_PROVIDER=api), end to end in
PostgreSQL/pgvector: written into its own generation, then found by a query.

The service is a stand-in over HTTP whose vectors come from the deterministic embedder,
so nearness by meaning is real enough to rank, and no network is used."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.adapters.embedding_api import ApiTextEmbedding
from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.adapters.embedding_generations import PgEmbeddingIndexStore
from backend.adapters.vector import PgVectorStore
from backend.domain.embedding import EmbeddingTask, TextInput

pytestmark = pytest.mark.integration

W, P = "ws_api", "prj_api"
DIMENSION = 256


class StandIn:
    def __init__(self) -> None:
        self.words = DeterministicEmbedding(dimension=DIMENSION, model="stand-in")
        self.sent: list[list[str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["dimensions"] == DIMENSION
        self.sent.append(list(body["input"]))
        vectors = self.words.embed(
            [TextInput(text=text) for text in body["input"]], task=EmbeddingTask.DOCUMENT
        )
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": list(vector.values)}
                    for index, vector in enumerate(vectors)
                ]
            },
        )


def test_passages_are_written_and_found_through_the_service(disposable_database) -> None:  # type: ignore[no-untyped-def]
    dsn = disposable_database.psycopg_dsn
    store = PgEmbeddingIndexStore(dsn)
    service = StandIn()
    embedder = ApiTextEmbedding(
        base_url="https://embed.example.com/v1",
        api_key="sk-test-not-real",
        model="text-embedding-3-small",
        dimension=DIMENSION,
        transport=httpx.MockTransport(service),
    )
    vectors = PgVectorStore(dsn, embedder, index=store)
    vectors.add_documents(
        ["the lighthouse lantern went dark", "bread rising in the bakery oven"],
        [{"kind": "passage"}, {"kind": "passage"}],
        ["p_lamp", "p_bread"],
        workspace_id=W,
        project_id=P,
    )

    active = store.active()
    assert active is not None
    assert (active.model, active.revision, active.dimension) == (
        "text-embedding-3-small",
        "api:embed.example.com",
        DIMENSION,
    )
    assert store.count(active.id) == 2
    assert vectors.searchable() is not None

    found = vectors.query("lantern", workspace_id=W, project_id=P, n_results=2)
    assert [hit["id"] for hit in found][0] == "p_lamp"
    # Both passages went to the service once, and then the query on its own.
    assert service.sent[0] == [
        "the lighthouse lantern went dark",
        "bread rising in the bakery oven",
    ]
    assert service.sent[-1] == ["lantern"]


def test_a_service_that_fails_leaves_search_empty_rather_than_wrong(disposable_database) -> None:  # type: ignore[no-untyped-def]
    dsn = disposable_database.psycopg_dsn
    store = PgEmbeddingIndexStore(dsn)
    working = ApiTextEmbedding(
        base_url="https://embed.example.com/v1",
        api_key="sk-test-not-real",
        model="text-embedding-3-small",
        dimension=DIMENSION,
        transport=httpx.MockTransport(StandIn()),
    )
    PgVectorStore(dsn, working, index=store).add_documents(
        ["the lighthouse lantern went dark"],
        [{"kind": "passage"}],
        ["p_lamp"],
        workspace_id=W,
        project_id=P,
    )
    failing = ApiTextEmbedding(
        base_url="https://embed.example.com/v1",
        api_key="sk-test-not-real",
        model="text-embedding-3-small",
        dimension=DIMENSION,
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        sleep=lambda seconds: None,
    )
    assert (
        PgVectorStore(dsn, failing, index=store).query("lantern", workspace_id=W, project_id=P)
        == []
    )
