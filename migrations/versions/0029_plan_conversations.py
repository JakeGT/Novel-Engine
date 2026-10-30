"""conversations about the story plan

Revision ID: 0029_plan_conversations
Revises: 0028_plan_items
Create Date: 2026-10-11

A planning conversation (what the author said, what the assistant asked, what was
answered) is kept like any other, as the evidence behind the plan it led to.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029_plan_conversations"
down_revision: str | None = "0028_plan_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE assist_sessions DROP CONSTRAINT IF EXISTS assist_sessions_kind_check")
    op.execute(
        "ALTER TABLE assist_sessions ADD CONSTRAINT assist_sessions_kind_check "
        "CHECK (kind IN ('create', 'fill_in', 'change', 'plan'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM assist_sessions WHERE kind = 'plan'")
    op.execute("ALTER TABLE assist_sessions DROP CONSTRAINT IF EXISTS assist_sessions_kind_check")
    op.execute(
        "ALTER TABLE assist_sessions ADD CONSTRAINT assist_sessions_kind_check "
        "CHECK (kind IN ('create', 'fill_in', 'change'))"
    )
