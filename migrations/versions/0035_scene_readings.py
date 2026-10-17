"""Readings of a scene into the story (Phase 6).

Each time a scene's text is read into proposed changes, ``scene_readings`` records which
version was read, by which job, which passages have been read so far and which proposals
came of them, per line of the story. It is written as the reading goes, so a reading that
stops partway is resumed rather than repeated. The next reading reads only what changed
since; the studio says when a scene has changed since it was last read. Copied with a
what-if, like the scene links.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035_scene_readings"
down_revision: str | None = "0034_scene_studio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_readings (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            job_id TEXT,
            passages INTEGER NOT NULL DEFAULT 0,
            proposals JSONB NOT NULL DEFAULT '[]'::jsonb,
            discarded INTEGER NOT NULL DEFAULT 0,
            -- 'partial' while it runs or when it stopped; 'complete' when every passage was read.
            status TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('partial', 'complete')),
            -- The [start, end] spans of the version already read, so a retry resumes.
            covered JSONB NOT NULL DEFAULT '[]'::jsonb,
            -- The complete reading this one started from.
            base_reading_id TEXT,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, branch_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS scene_readings_latest "
        "ON scene_readings (workspace_id, project_id, branch_id, plan_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scene_readings")
