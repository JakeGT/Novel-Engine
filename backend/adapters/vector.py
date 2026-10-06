from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from backend.core.logging import instrument
from backend.ports import EmbeddingPort

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSION = 384


class PgVectorStore:
    """Production vector store backed by PostgreSQL/pgvector.

    Documents are derived and rebuildable. Embeddings are produced through the
    injected embedding port, so this adapter never loads a model itself.
    """

    def __init__(
        self,
        dsn: str,
        embeddings: EmbeddingPort,
        dimension: int = EMBEDDING_DIMENSION,
    ) -> None:
        self._dsn = dsn
        self._embeddings = embeddings
        self._dimension = dimension

    def _connect(self) -> psycopg.Connection:
        connection = psycopg.connect(self._dsn)
        register_vector(connection)
        return connection

    @instrument("vector.add_documents", logger)
    def add_documents(
        self, documents: list[str], metadatas: list[dict[str, Any]], ids: list[str]
    ) -> None:
        with self._connect() as connection:
            for document_id, document, metadata in zip(ids, documents, metadatas, strict=True):
                embedding = self._embeddings.embed(document)
                if embedding is None:
                    raise RuntimeError(
                        "Embedding provider returned no vector; cannot index document "
                        f"{document_id!r}"
                    )
                connection.execute(
                    """
                    INSERT INTO vector_documents (id, document, metadata, embedding)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                        SET document = EXCLUDED.document,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding
                    """,
                    (document_id, document, Jsonb(metadata), Vector(embedding)),
                )

    @instrument("vector.query", logger)
    def query(self, query_text: str, n_results: int = 5) -> list[dict[str, Any]]:
        embedding = self._embeddings.embed(query_text)
        if embedding is None:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, document, metadata, 1 - (embedding <=> %s) AS score
                FROM vector_documents
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (Vector(embedding), Vector(embedding), n_results),
            ).fetchall()
        return [
            {"id": row[0], "document": row[1], "metadata": row[2], "score": row[3]} for row in rows
        ]

    @instrument("vector.delete", logger)
    def delete(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM vector_documents WHERE id = ANY(%s)", (list(ids),))

    def health(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1 FROM vector_documents LIMIT 1")
            return True
        except Exception:
            return False
