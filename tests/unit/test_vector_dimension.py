import pytest

from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.adapters.vector import PgVectorStore, VectorStoreError
from tests.doubles import FakeEmbeddingProvider
from tests.embedding_doubles import InMemoryEmbeddingIndexStore

pytestmark = pytest.mark.unit


def test_configured_size_must_be_the_models_own() -> None:
    store = PgVectorStore(
        "postgresql://127.0.0.1:1/unused",
        FakeEmbeddingProvider(dimension=384),
        dimension=768,
        index=InMemoryEmbeddingIndexStore(),
    )
    assert store.dimension_supported() is False
    assert store.health() is False
    with pytest.raises(VectorStoreError, match="EMBEDDING_DIMENSION=768"):
        store.add_documents(["x"], [{}], ["doc_x"], workspace_id="ws", project_id="ws")


def test_any_size_a_generation_holds_is_supported() -> None:
    store = PgVectorStore(
        "postgresql://127.0.0.1:1/unused",
        FakeEmbeddingProvider(dimension=768),
        index=InMemoryEmbeddingIndexStore(),
    )
    assert store.dimension_supported() is True


def test_first_write_starts_a_generation_in_the_models_space() -> None:
    index = InMemoryEmbeddingIndexStore()
    store = PgVectorStore("unused", DeterministicEmbedding(dimension=512), index=index)
    store.add_documents(
        ["the basalt coast"], [{"kind": "passage"}], ["p1"], workspace_id="w", project_id="p"
    )
    active = index.active()
    assert active is not None and active.dimension == 512
    assert store.query("basalt coast", workspace_id="w", project_id="p")[0]["id"] == "p1"
    assert store.health() is True


def test_a_different_model_never_searches_the_index_in_use() -> None:
    index = InMemoryEmbeddingIndexStore()
    first = PgVectorStore("unused", DeterministicEmbedding(dimension=256, model="a"), index=index)
    first.add_documents(["the basalt coast"], [{}], ["p1"], workspace_id="w", project_id="p")
    second = PgVectorStore("unused", DeterministicEmbedding(dimension=256, model="b"), index=index)
    # Same size, different model: the spaces differ, so nothing is compared.
    assert second.query("basalt coast", workspace_id="w", project_id="p") == []
    assert second.health() is False
    # Writing with the other model removes the stale row rather than mixing spaces.
    second.add_documents(["new words"], [{}], ["p1"], workspace_id="w", project_id="p")
    assert first.query("basalt coast", workspace_id="w", project_id="p") == []
