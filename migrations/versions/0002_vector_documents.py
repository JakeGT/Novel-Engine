"""pgvector derived document index

Revision ID: 0002_vector_documents
Revises: 0001_initial
Create Date: 2026-10-05

Creates the derived, rebuildable vector document table used by the production
pgvector adapter. Rows are never canonical; they can be deleted and rebuilt from
canonical graph/prose sources.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_vector_documents"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSION = 384


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS vector_documents (
            id TEXT PRIMARY KEY,
            document TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            embedding vector({EMBEDDING_DIMENSION}) NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS vector_documents_embedding_idx "
        "ON vector_documents USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vector_documents")
