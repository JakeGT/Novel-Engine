"""which pass of a check found each note, and finding an audit's notes

Revision ID: 0039_check_passes
Revises: 0038_usage_controls
Create Date: 2026-10-18

CON-008. A check with the assistant is now made of passes (viewpoint and secrets,
continuity, the plan, voice, clarity), one model call each; ``draft_findings.check_pass``
records the pass that found a note. Notes from the earlier all-in-one check keep a null
pass and are shown as they were.

CON-011. A deep audit stores its notes against the exact scene versions it checked,
under its own job; the index finds them by job.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_check_passes"
down_revision: str | None = "0038_usage_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE draft_findings ADD COLUMN IF NOT EXISTS check_pass TEXT "
        "CHECK (check_pass IS NULL OR check_pass IN "
        "('viewpoint', 'continuity', 'plan', 'voice', 'clarity'))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS draft_findings_job ON draft_findings "
        "(workspace_id, project_id, job_id) WHERE job_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS draft_findings_job")
    op.execute("ALTER TABLE draft_findings DROP COLUMN IF EXISTS check_pass")
