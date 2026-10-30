"""picture processing: smaller copies, the whole-picture segment and processing state

Revision ID: 0046_media_variants
Revises: 0045_how_long
Create Date: 2026-10-18

AST-003 / AST-006 (images only). Every stored picture version is decoded once, in a
separate worker process with memory, CPU and time limits, and turned into rebuildable
smaller copies (a 256px thumbnail and a 1024px web copy, both WebP with no metadata).

- ``media_processing``: one row per asset version: queued, processing, ready or failed,
  the processor version and source checksum it ran with (the idempotency key), the
  decoded size, the number of attempts and a plain failure code (never content).
- ``media_variants``: the smaller copies, keyed by version and kind. Rebuildable, so they
  are never exported; a restore queues processing again.
- ``media_segments``: retrievable portions of a version: the whole picture (made by the
  processor) and optional author-selected rectangular regions. Later areas (observations,
  embeddings) point at a segment.

Chained after 0045_how_long, the head when this was written.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0046_media_variants"
down_revision: str | None = "0045_how_long"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_processing (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            asset_version_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
            processor_version TEXT NOT NULL,
            source_checksum TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            failure_code TEXT,
            width INTEGER,
            height INTEGER,
            detected_format TEXT,
            job_id TEXT,
            queued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, asset_version_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS media_processing_status_idx "
        "ON media_processing (status, queued_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_variants (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            asset_version_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('thumbnail', 'web')),
            object_key TEXT NOT NULL,
            content_type TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            byte_length BIGINT NOT NULL,
            checksum TEXT NOT NULL,
            source_checksum TEXT NOT NULL,
            processor_version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, asset_version_id, kind)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_segments (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            asset_version_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('whole', 'region')),
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            width INTEGER NOT NULL CHECK (width > 0),
            height INTEGER NOT NULL CHECK (height > 0),
            label TEXT,
            content_hash TEXT NOT NULL,
            created_by TEXT,
            processor_version TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            retired_at TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS media_segments_whole_uq "
        "ON media_segments (workspace_id, project_id, asset_version_id) WHERE kind = 'whole'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS media_segments_version_idx "
        "ON media_segments (workspace_id, project_id, asset_version_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS media_segments")
    op.execute("DROP TABLE IF EXISTS media_variants")
    op.execute("DROP TABLE IF EXISTS media_processing")
