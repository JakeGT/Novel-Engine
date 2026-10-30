"""audits and impact: notices for the author when a scheduled audit finishes

Revision ID: 0062_audits_and_impact
Revises: 0061_check_passes_more
Create Date: 2026-10-21

CON-011. ``author_notices`` holds an in-app notice for the author a scheduled audit ran
for, once it finishes (succeeded, stopped or failed): which audit and schedule, the line
of the story, how many scenes it read, how many notes the assistant and the checks
without it found, and what it used (tokens, and the provider's cost when it said). Counts
and identifiers only, never the story's words: the part of the plan is named when the
notice is read. One notice per audit (``job_id`` is unique), so a finish seen twice
records it once. ``seen_at`` is set when the author marks it read. Deleted with its
project; an archive does not carry it (a notice is about this site's jobs, which an
archive leaves out).

Chained after 0061_check_passes_more.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0062_audits_and_impact"
down_revision: str | None = "0061_check_passes_more"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS author_notices (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('audit_finished')),
            branch_id TEXT,
            job_id TEXT NOT NULL,
            schedule_id TEXT,
            plan_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'cancelled')),
            scenes INTEGER NOT NULL DEFAULT 0 CHECK (scenes >= 0),
            notes INTEGER NOT NULL DEFAULT 0 CHECK (notes >= 0),
            checks INTEGER NOT NULL DEFAULT 0 CHECK (checks >= 0),
            prompt_tokens INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
            completion_tokens INTEGER NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
            cost_usd DOUBLE PRECISION CHECK (cost_usd IS NULL OR cost_usd >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            seen_at TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, id),
            UNIQUE (workspace_id, project_id, job_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS author_notices_for "
        "ON author_notices (workspace_id, project_id, user_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS author_notices")
