import pytest

from backend.seed.apply import apply_manifest, run_expected_queries
from backend.seed.world import REFERENCE_WORLD
from tests.doubles import (
    DisabledEmbeddingProvider,
    FakeEmbeddingProvider,
    InMemoryGraphRepository,
    InMemoryVectorStore,
)

pytestmark = pytest.mark.application


def test_apply_manifest_is_idempotent_and_verifies() -> None:
    graph = InMemoryGraphRepository()

    first = apply_manifest(graph, REFERENCE_WORLD)
    second = apply_manifest(graph, REFERENCE_WORLD)

    assert first.ok, first.problems
    assert second.ok, second.problems
    assert first.applied_nodes == len(REFERENCE_WORLD.nodes)
    assert first.applied_edges == len(REFERENCE_WORLD.edges)
    assert len(graph.nodes) == len(REFERENCE_WORLD.nodes)
    assert len(graph.edges) == len(REFERENCE_WORLD.edges)


def test_expected_queries_match_seeded_graph() -> None:
    graph = InMemoryGraphRepository()
    apply_manifest(graph, REFERENCE_WORLD)

    results = run_expected_queries(graph, REFERENCE_WORLD)
    for query in REFERENCE_WORLD.queries:
        assert results[query.id] == sorted(query.expected_ids)


def test_documents_indexed_with_available_embeddings() -> None:
    graph = InMemoryGraphRepository()
    vectors = InMemoryVectorStore()

    report = apply_manifest(
        graph,
        REFERENCE_WORLD,
        vectors=vectors,
        embeddings=FakeEmbeddingProvider(),
    )

    assert report.indexed_documents == len(REFERENCE_WORLD.documents)
    assert report.skipped_documents == 0
    assert len(vectors.documents) == len(REFERENCE_WORLD.documents)
    assert all(doc_id.startswith("seed:") for doc_id in vectors.documents)


def test_documents_skipped_without_embeddings() -> None:
    graph = InMemoryGraphRepository()
    vectors = InMemoryVectorStore()

    report = apply_manifest(
        graph,
        REFERENCE_WORLD,
        vectors=vectors,
        embeddings=DisabledEmbeddingProvider(),
    )

    assert report.indexed_documents == 0
    assert report.skipped_documents == len(REFERENCE_WORLD.documents)
    assert vectors.documents == {}
