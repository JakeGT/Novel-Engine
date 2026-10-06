"""workspace scope for derived vectors

Revision ID: 0007_workspace_scope
Revises: 0006_rate_limiter
Create Date: 2026-10-07

Adds the owning workspace (and optional project) to derived vector rows so every
pgvector read, write, and delete is confined to one workspace. The default empty
string backfills pre-existing rows, which are then unreachable until re-indexed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_workspace_scope"
down_revision: str | None = "0006_rate_limiter"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE vector_documents "
        "ADD COLUMN IF NOT EXISTS workspace_id TEXT NOT NULL DEFAULT ''"
    )
    op.execute("ALTER TABLE vector_documents ADD COLUMN IF NOT EXISTS project_id TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS vector_documents_workspace_idx "
        "ON vector_documents (workspace_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS vector_documents_workspace_idx")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS project_id")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS workspace_id")
