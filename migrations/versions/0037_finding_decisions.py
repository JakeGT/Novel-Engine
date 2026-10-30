"""dismissed findings in the studio, and story rules that block

Revision ID: 0037_finding_decisions
Revises: 0036_cached_tokens
Create Date: 2026-10-17

CON-010 and CON-016. ``finding_dismissals`` keeps the author's decision to dismiss a
check or an assistant's note on a scene, with a reason, per line of the story (copied
with a what-if). It holds a fingerprint of the words the finding pointed at, never the
words. Undoing a dismissal marks it withdrawn; rows are otherwise never changed.

CON-015. ``story_rules.enforcement`` says whether a rule only warns (the default, so
every existing rule keeps its meaning) or blocks a change that would break it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0037_finding_decisions"
down_revision: str | None = "0036_cached_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finding_dismissals (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
            kind TEXT NOT NULL CHECK (kind IN ('check', 'note')),
            code TEXT NOT NULL,
            reason TEXT NOT NULL CHECK (reason IN ('intended', 'not_a_problem', 'other')),
            note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200),
            expires_at TIMESTAMPTZ,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            withdrawn_at TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, branch_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS finding_dismissals_scene "
        "ON finding_dismissals (workspace_id, project_id, branch_id, plan_id, created_at)"
    )
    # At most one standing dismissal of the same finding in a scene.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS finding_dismissals_standing "
        "ON finding_dismissals (workspace_id, project_id, branch_id, plan_id, fingerprint) "
        "WHERE withdrawn_at IS NULL"
    )
    op.execute(
        "ALTER TABLE story_rules ADD COLUMN IF NOT EXISTS enforcement TEXT NOT NULL "
        "DEFAULT 'warning' CHECK (enforcement IN ('warning', 'blocking'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE story_rules DROP COLUMN IF EXISTS enforcement")
    op.execute("DROP TABLE IF EXISTS finding_dismissals")
