"""what the assistant thought a project's setup was missing

Revision ID: 0026_assist_suggestions
Revises: 0025_workspace_ai_settings
Create Date: 2026-10-10

Kept with the conversation that produced them, so they can be read and acted on
after the dialog is closed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026_assist_suggestions"
down_revision: str | None = "0025_workspace_ai_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE assist_sessions "
        "ADD COLUMN IF NOT EXISTS suggestions JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE assist_sessions DROP COLUMN IF EXISTS suggestions")
