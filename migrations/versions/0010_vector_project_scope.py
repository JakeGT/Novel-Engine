"""project scope for derived vectors

Revision ID: 0010_vector_project_scope
Revises: 0009_provider_usage
Create Date: 2026-10-07

Makes the owning project mandatory on derived vector rows and moves the primary
key to (workspace_id, project_id, id) so an identical document id in two projects
of the same workspace cannot overwrite the other. Existing rows are backfilled to
an empty project and become unreachable until re-indexed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_vector_project_scope"
down_revision: str | None = "0009_provider_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE vector_documents SET project_id = '' WHERE project_id IS NULL")
    op.execute("ALTER TABLE vector_documents ALTER COLUMN project_id SET DEFAULT ''")
    op.execute("ALTER TABLE vector_documents ALTER COLUMN project_id SET NOT NULL")
    op.execute("ALTER TABLE vector_documents DROP CONSTRAINT IF EXISTS vector_documents_pkey")
    op.execute(
        "ALTER TABLE vector_documents ADD PRIMARY KEY (workspace_id, project_id, id)"
    )
    op.execute("DROP INDEX IF EXISTS vector_documents_workspace_idx")
    op.execute(
        "CREATE INDEX IF NOT EXISTS vector_documents_scope_idx "
        "ON vector_documents (workspace_id, project_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE vector_documents DROP CONSTRAINT IF EXISTS vector_documents_pkey")
    op.execute("ALTER TABLE vector_documents ADD PRIMARY KEY (id)")
    op.execute("DROP INDEX IF EXISTS vector_documents_scope_idx")
    op.execute(
        "CREATE INDEX IF NOT EXISTS vector_documents_workspace_idx "
        "ON vector_documents (workspace_id)"
    )
    op.execute("ALTER TABLE vector_documents ALTER COLUMN project_id DROP NOT NULL")