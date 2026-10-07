import psycopg
import pytest

from backend.adapters.vector import PgVectorStore
from tests.doubles import FakeEmbeddingProvider

pytestmark = pytest.mark.integration

WS = "ws-test"
PROJECT = "prj-test"
OTHER_WS = "ws-other"
OTHER_PROJECT = "prj-other"


@pytest.fixture
def vector_store(disposable_database) -> PgVectorStore:
    return PgVectorStore(disposable_database.psycopg_dsn, FakeEmbeddingProvider())


def test_vector_store_contract(vector_store: PgVectorStore) -> None:
    vector_store.add_documents(
        documents=["Mara guards the lantern", "Ivo forges the lens"],
        metadatas=[{"entity": "char_mara"}, {"entity": "char_ivo"}],
        ids=["doc_mara", "doc_ivo"],
        workspace_id=WS,
        project_id=PROJECT,
    )

    results = vector_store.query(
        "Mara guards the lantern", workspace_id=WS, project_id=PROJECT, n_results=2
    )
    assert results[0]["id"] == "doc_mara"
    assert results[0]["document"] == "Mara guards the lantern"
    assert results[0]["metadata"] == {"entity": "char_mara"}
    assert results[0]["score"] > 0.99

    vector_store.add_documents(
        documents=["Mara guards the lantern at night"],
        metadatas=[{"entity": "char_mara", "revised": True}],
        ids=["doc_mara"],
        workspace_id=WS,
        project_id=PROJECT,
    )
    upserted = vector_store.query(
        "Mara guards the lantern at night", workspace_id=WS, project_id=PROJECT, n_results=1
    )[0]
    assert upserted["id"] == "doc_mara"
    assert upserted["document"] == "Mara guards the lantern at night"
    assert upserted["metadata"]["revised"] is True

    vector_store.delete(["doc_mara", "doc_ivo"], workspace_id=WS, project_id=PROJECT)
    assert (
        vector_store.query(
            "Mara guards the lantern", workspace_id=WS, project_id=PROJECT, n_results=5
        )
        == []
    )
    assert vector_store.health() is True


def test_vector_store_health_false_when_table_missing(disposable_database) -> None:
    with psycopg.connect(disposable_database.psycopg_dsn, autocommit=True) as connection:
        connection.execute("DROP TABLE vector_documents")

    store = PgVectorStore(disposable_database.psycopg_dsn, FakeEmbeddingProvider())
    assert store.health() is False


def test_vector_store_records_embedding_provenance(disposable_database) -> None:
    embeddings = FakeEmbeddingProvider(model="fake-embedder", version="7")
    store = PgVectorStore(disposable_database.psycopg_dsn, embeddings)

    store.add_documents(
        ["provenance check"],
        [{"entity": "char_mara"}],
        ["doc_prov"],
        workspace_id=WS,
        project_id=PROJECT,
    )

    result = store.query("provenance check", workspace_id=WS, project_id=PROJECT, n_results=1)[0]
    assert result["embedding_model"] == "fake-embedder"
    assert result["embedding_version"] == "7"
    assert result["embedding_dimension"] == 384

    with psycopg.connect(disposable_database.psycopg_dsn) as connection:
        row = connection.execute(
            "SELECT embedding_model, embedding_version, embedding_dimension, project_id "
            "FROM vector_documents WHERE id = 'doc_prov'"
        ).fetchone()
    assert row == ("fake-embedder", "7", 384, PROJECT)


def test_vector_store_isolates_workspaces(vector_store: PgVectorStore) -> None:
    vector_store.add_documents(
        documents=["shared text"],
        metadatas=[{"scope": WS}],
        ids=["doc_ws"],
        workspace_id=WS,
        project_id=PROJECT,
    )
    vector_store.add_documents(
        documents=["shared text"],
        metadatas=[{"scope": OTHER_WS}],
        ids=["doc_other"],
        workspace_id=OTHER_WS,
        project_id=PROJECT,
    )

    ws_ids = {
        r["id"]
        for r in vector_store.query("shared text", workspace_id=WS, project_id=PROJECT, n_results=5)
    }
    other_ids = {
        r["id"]
        for r in vector_store.query(
            "shared text", workspace_id=OTHER_WS, project_id=PROJECT, n_results=5
        )
    }
    assert ws_ids == {"doc_ws"}
    assert other_ids == {"doc_other"}

    vector_store.delete(["doc_ws"], workspace_id=OTHER_WS, project_id=PROJECT)
    assert {
        r["id"]
        for r in vector_store.query("shared text", workspace_id=WS, project_id=PROJECT, n_results=5)
    } == {"doc_ws"}


def test_vector_store_isolates_projects_in_same_workspace(vector_store: PgVectorStore) -> None:
    # Same document id in two projects of one workspace must coexist, not overwrite.
    vector_store.add_documents(
        documents=["project one text"],
        metadatas=[{"p": PROJECT}],
        ids=["doc_shared"],
        workspace_id=WS,
        project_id=PROJECT,
    )
    vector_store.add_documents(
        documents=["project two text"],
        metadatas=[{"p": OTHER_PROJECT}],
        ids=["doc_shared"],
        workspace_id=WS,
        project_id=OTHER_PROJECT,
    )

    p1 = vector_store.query("text", workspace_id=WS, project_id=PROJECT, n_results=5)
    p2 = vector_store.query("text", workspace_id=WS, project_id=OTHER_PROJECT, n_results=5)
    assert {r["document"] for r in p1} == {"project one text"}
    assert {r["document"] for r in p2} == {"project two text"}

    # Deleting in one project does not affect the sibling project.
    vector_store.delete(["doc_shared"], workspace_id=WS, project_id=PROJECT)
    assert vector_store.query("text", workspace_id=WS, project_id=PROJECT, n_results=5) == []
    assert {
        r["id"]
        for r in vector_store.query("text", workspace_id=WS, project_id=OTHER_PROJECT, n_results=5)
    } == {"doc_shared"}
