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

# Must match migrations/versions/0002_vector_documents.py (vector(384)). Changing
# the schema dimension requires a new migration, so a mismatch is refused rather
# than allowed to fail at insert time.
SCHEMA_VECTOR_DIMENSION = 384
EMBEDDING_DIMENSION = SCHEMA_VECTOR_DIMENSION


class VectorStoreError(RuntimeError):
    """Raised when the vector store cannot satisfy the configured embedding contract."""


class PgVectorStore:
    """Production vector store backed by PostgreSQL/pgvector.

    Documents are derived and rebuildable. Embeddings are produced through the
    injected embedding port, so this adapter never loads a model itself. Each row
    records the embedding model, version, and dimension for provenance.
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

    def dimension_supported(self) -> bool:
        return self._dimension == SCHEMA_VECTOR_DIMENSION

    def _require_supported_dimension(self) -> None:
        if not self.dimension_supported():
            raise VectorStoreError(
                f"configured EMBEDDING_DIMENSION={self._dimension} does not match the "
                f"vector({SCHEMA_VECTOR_DIMENSION}) schema; add a migration before changing it"
            )

    def _connect(self) -> psycopg.Connection:
        connection = psycopg.connect(self._dsn)
        register_vector(connection)
        return connection

    @instrument("vector.add_documents", logger)
    def add_documents(
        self,
        documents: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str],
        *,
        workspace_id: str,
        project_id: str,
    ) -> None:
        self._require_supported_dimension()
        with self._connect() as connection:
            for document_id, document, metadata in zip(ids, documents, metadatas, strict=True):
                embedding = self._embeddings.embed(document)
                if embedding is None:
                    raise VectorStoreError(
                        "Embedding provider returned no vector; cannot index document "
                        f"{document_id!r}"
                    )
                if len(embedding) != self._dimension:
                    raise VectorStoreError(
                        f"embedding provider returned dimension {len(embedding)}, "
                        f"expected {self._dimension}"
                    )
                connection.execute(
                    """
                    INSERT INTO vector_documents (
                        id, workspace_id, project_id, document, metadata, embedding,
                        embedding_model, embedding_version, embedding_dimension
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, project_id, id) DO UPDATE
                        SET document = EXCLUDED.document,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding,
                            embedding_model = EXCLUDED.embedding_model,
                            embedding_version = EXCLUDED.embedding_version,
                            embedding_dimension = EXCLUDED.embedding_dimension
                    """,
                    (
                        document_id,
                        workspace_id,
                        project_id,
                        document,
                        Jsonb(metadata),
                        Vector(embedding),
                        self._embeddings.model,
                        self._embeddings.version,
                        self._dimension,
                    ),
                )

    @instrument("vector.query", logger)
    def query(
        self,
        query_text: str,
        *,
        workspace_id: str,
        project_id: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        self._require_supported_dimension()
        embedding = self._embeddings.embed(query_text)
        if embedding is None:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, document, metadata, 1 - (embedding <=> %s) AS score,
                       embedding_model, embedding_version, embedding_dimension
                FROM vector_documents
                WHERE workspace_id = %s AND project_id = %s
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (Vector(embedding), workspace_id, project_id, Vector(embedding), n_results),
            ).fetchall()
        return [
            {
                "id": row[0],
                "document": row[1],
                "metadata": row[2],
                "score": row[3],
                "embedding_model": row[4],
                "embedding_version": row[5],
                "embedding_dimension": row[6],
            }
            for row in rows
        ]

    @instrument("vector.delete", logger)
    def delete(self, ids: Sequence[str], *, workspace_id: str, project_id: str) -> None:
        if not ids:
            return
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM vector_documents "
                "WHERE id = ANY(%s) AND workspace_id = %s AND project_id = %s",
                (list(ids), workspace_id, project_id),
            )

    @instrument("vector.delete_workspace", logger)
    def delete_workspace(self, *, workspace_id: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM vector_documents WHERE workspace_id = %s", (workspace_id,)
            ).rowcount

    @instrument("vector.delete_project", logger)
    def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM vector_documents WHERE workspace_id = %s AND project_id = %s",
                (workspace_id, project_id),
            ).rowcount

    def health(self) -> bool:
        if not self.dimension_supported():
            return False
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1 FROM vector_documents LIMIT 1")
            return True
        except Exception:
            return False
