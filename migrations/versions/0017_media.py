"""media identity and explicit asset links

Revision ID: 0017_media
Revises: 0016_canon_tags
Create Date: 2026-10-08

Phase 2 media identity: project-scoped asset records, immutable asset versions
addressed by opaque object keys, and revisioned explicit links. PostgreSQL is the
lifecycle authority; bytes live in the scoped object store. No processing/scanning
state is modelled here (Phase 4+).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_media"
down_revision: str | None = "0016_canon_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_assets (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            name TEXT NOT NULL,
            lifecycle TEXT NOT NULL DEFAULT 'active',
            created_revision BIGINT NOT NULL,
            retired_revision BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_asset_versions (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            object_key TEXT NOT NULL,
            content_type TEXT NOT NULL,
            byte_length BIGINT NOT NULL,
            checksum TEXT NOT NULL,
            source TEXT,
            licence TEXT,
            attribution TEXT,
            authority TEXT,
            visibility TEXT,
            created_revision BIGINT NOT NULL,
            retired_revision BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id),
            UNIQUE (workspace_id, project_id, asset_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_links (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            asset_version_id TEXT,
            role TEXT NOT NULL,
            target_record_type TEXT NOT NULL,
            target_record_id TEXT NOT NULL,
            provenance_id TEXT,
            created_revision BIGINT NOT NULL,
            retired_revision BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS asset_links_asset_idx "
        "ON asset_links (workspace_id, project_id, asset_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS asset_links")
    op.execute("DROP TABLE IF EXISTS media_asset_versions")
    op.execute("DROP TABLE IF EXISTS media_assets")
