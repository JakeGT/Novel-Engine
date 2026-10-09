"""archive import status ledger

Revision ID: 0022_archive_imports
Revises: 0021_media_lifecycle
Create Date: 2026-10-09

One row per (project, archive checksum). An import is recorded before its first
durable write and only marked ``completed`` after every commit has replayed and
been verified, so a partial restore is never presented as a successful project.
The row holds counts, the opaque keys of objects this import stored (so a failed
import can be removed exactly), and an error class; never archive content.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_archive_imports"
down_revision: str | None = "0021_media_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_imports (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            archive_checksum TEXT NOT NULL,
            format_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'importing', 'completed', 'failed', 'removed'
            )),
            stage TEXT NOT NULL DEFAULT 'validated',
            commits_total INTEGER NOT NULL DEFAULT 0,
            commits_applied INTEGER NOT NULL DEFAULT 0,
            objects_imported INTEGER NOT NULL DEFAULT 0,
            object_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
            error TEXT,
            actor_user_id TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE (workspace_id, project_id, archive_checksum)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS archive_imports_scope_idx "
        "ON archive_imports (workspace_id, project_id, started_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS archive_imports")
