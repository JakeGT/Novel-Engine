"""embedding provenance columns

Revision ID: 0003_embedding_provenance
Revises: 0002_vector_documents
Create Date: 2026-10-06

Records the embedding model, version, and dimension on each derived vector row so
vectors can be identified and rebuilt when the embedding model changes. Columns
are nullable so rows written before this revision remain readable until rebuilt.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_embedding_provenance"
down_revision: str | None = "0002_vector_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vector_documents", sa.Column("embedding_model", sa.Text(), nullable=True))
    op.add_column("vector_documents", sa.Column("embedding_version", sa.Text(), nullable=True))
    op.add_column("vector_documents", sa.Column("embedding_dimension", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("vector_documents", "embedding_dimension")
    op.drop_column("vector_documents", "embedding_version")
    op.drop_column("vector_documents", "embedding_model")
