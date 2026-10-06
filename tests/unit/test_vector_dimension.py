import pytest

from backend.adapters.vector import SCHEMA_VECTOR_DIMENSION, PgVectorStore, VectorStoreError
from tests.doubles import FakeEmbeddingProvider

pytestmark = pytest.mark.unit


def test_schema_dimension_matches_migration_contract() -> None:
    assert SCHEMA_VECTOR_DIMENSION == 384


def test_unsupported_dimension_refused_without_connecting() -> None:
    store = PgVectorStore(
        "postgresql://127.0.0.1:1/unused",
        FakeEmbeddingProvider(dimension=768),
        dimension=768,
    )
    assert store.dimension_supported() is False
    assert store.health() is False
    with pytest.raises(VectorStoreError, match="EMBEDDING_DIMENSION=768"):
        store.add_documents(["x"], [{}], ["doc_x"])


def test_supported_dimension_reports_supported() -> None:
    store = PgVectorStore("postgresql://127.0.0.1:1/unused", FakeEmbeddingProvider())
    assert store.dimension_supported() is True
