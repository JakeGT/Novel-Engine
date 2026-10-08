"""project schema versions

Revision ID: 0012_schema_versions
Revises: 0011_active_credential_unique
Create Date: 2026-10-08

Immutable, project-scoped schema versions. A project drafts a bounded declarative
definition and publishes sequential immutable versions; canon retains the version
under which it was accepted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_schema_versions"
down_revision: str | None = "0011_active_credential_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            definition JSONB NOT NULL,
            content_hash TEXT NOT NULL,
            compatibility TEXT NOT NULL,
            created_by_user_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            published_at TIMESTAMPTZ,
            UNIQUE (workspace_id, project_id, version)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS schema_versions_project_idx "
        "ON schema_versions (workspace_id, project_id, version DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS schema_versions")