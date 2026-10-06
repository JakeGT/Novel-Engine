import os
from collections.abc import Sequence
from typing import Any

import chromadb

DEFAULT_PERSISTENCE_PATH = "./data/chroma"


class VectorStore:
    def __init__(self, persistence_path: str = DEFAULT_PERSISTENCE_PATH):
        self.persistence_path = persistence_path
        self._client = None
        self._collection = None

    @property
    def client(self):
        if self._client is None:
            self._client = chromadb.PersistentClient(path=self.persistence_path)
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(name="novel_engine")
        return self._collection

    def add_documents(self, documents: list[str], metadatas: list[dict[str, Any]], ids: list[str]):
        self.collection.add(documents=documents, metadatas=metadatas, ids=ids)

    def query(self, query_text: str, n_results: int = 5) -> list[dict[str, Any]]:
        results = self.collection.query(query_texts=[query_text], n_results=n_results)
        return results

    def delete(self, ids: Sequence[str]) -> None:
        if ids:
            self.collection.delete(ids=list(ids))

    def health(self) -> bool:
        if self._client is None and self._collection is None:
            return os.path.exists(self.persistence_path)
        try:
            self.collection.count()
            return True
        except Exception:
            return False


def create_vector_store(persistence_path: str | None = None) -> VectorStore:
    if persistence_path is None:
        persistence_path = os.environ.get("CHROMA_PERSISTENCE_PATH", DEFAULT_PERSISTENCE_PATH)
    return VectorStore(persistence_path=persistence_path)


# Transitional module-global instance; resolved lazily on first use.
vector_store = VectorStore()
