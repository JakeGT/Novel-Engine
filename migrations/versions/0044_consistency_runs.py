"""how a character came to know something, and checks kept as runs

Revision ID: 0044_consistency_runs
Revises: 0043_access_keys
Create Date: 2026-10-18

KNW-002. An event can say which scene it happens in (``scene_id``), and an event of
kind ``inferred`` holds the facts and beliefs a character worked something out from
(``premises``). Both are empty on every existing event, so nothing earlier changes.

CON-001. ``consistency_runs`` keeps each check as a record: what it covered (a scene,
a part of the plan or the whole story), the story's version and the exact text
versions it read, the passes it ran, and, for the checks that need no model, what they
found. Notes from the assistant stay in ``draft_findings`` under the run's job. Whether
a finding is still open, dismissed or fixed, and whether a run is out of date, is
worked out when it is read, never stored, so it cannot drift.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0044_consistency_runs"
down_revision: str | None = "0043_access_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE canon_events ADD COLUMN IF NOT EXISTS scene_id TEXT")
    op.execute("ALTER TABLE canon_events ADD COLUMN IF NOT EXISTS premises JSONB")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS consistency_runs (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('checks', 'assistant', 'audit')),
            scope TEXT NOT NULL CHECK (scope IN ('scene', 'part', 'story')),
            plan_id TEXT,
            story_revision INTEGER NOT NULL,
            inputs JSONB NOT NULL DEFAULT '[]'::jsonb,
            passes JSONB NOT NULL DEFAULT '[]'::jsonb,
            findings JSONB NOT NULL DEFAULT '[]'::jsonb,
            job_id TEXT,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS consistency_runs_line "
        "ON consistency_runs (workspace_id, project_id, branch_id, created_at)"
    )
    # One run per job: a job taken over by another worker records no second run.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS consistency_runs_job "
        "ON consistency_runs (workspace_id, project_id, job_id) WHERE job_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS consistency_runs")
    op.execute("ALTER TABLE canon_events DROP COLUMN IF EXISTS premises")
    op.execute("ALTER TABLE canon_events DROP COLUMN IF EXISTS scene_id")
