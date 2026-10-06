import psycopg
import pytest

from backend.adapters.vector import PgVectorStore
from tests.doubles import FakeEmbeddingProvider

pytestmark = pytest.mark.integration


@pytest.fixture
def vector_store(disposable_database) -> PgVectorStore:
    return PgVectorStore(disposable_database.psycopg_dsn, FakeEmbeddingProvider())


def test_vector_store_contract(vector_store: PgVectorStore) -> None:
    vector_store.add_documents(
        documents=["Mara guards the lantern", "Ivo forges the lens"],
        metadatas=[{"entity": "char_mara"}, {"entity": "char_ivo"}],
        ids=["doc_mara", "doc_ivo"],
    )

    results = vector_store.query("Mara guards the lantern", n_results=2)
    assert results[0]["id"] == "doc_mara"
    assert results[0]["document"] == "Mara guards the lantern"
    assert results[0]["metadata"] == {"entity": "char_mara"}
    assert results[0]["score"] > 0.99

    vector_store.add_documents(
        documents=["Mara guards the lantern at night"],
        metadatas=[{"entity": "char_mara", "revised": True}],
        ids=["doc_mara"],
    )
    upserted = vector_store.query("Mara guards the lantern at night", n_results=1)[0]
    assert upserted["id"] == "doc_mara"
    assert upserted["document"] == "Mara guards the lantern at night"
    assert upserted["metadata"]["revised"] is True

    vector_store.delete(["doc_mara", "doc_ivo"])
    assert vector_store.query("Mara guards the lantern", n_results=5) == []
    assert vector_store.health() is True


def test_vector_store_health_false_when_table_missing(disposable_database) -> None:
    with psycopg.connect(disposable_database.psycopg_dsn, autocommit=True) as connection:
        connection.execute("DROP TABLE vector_documents")

    store = PgVectorStore(disposable_database.psycopg_dsn, FakeEmbeddingProvider())
    assert store.health() is False
