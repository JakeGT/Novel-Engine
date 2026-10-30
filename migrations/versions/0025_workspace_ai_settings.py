"""which provider and model a workspace's assistant uses

Revision ID: 0025_workspace_ai_settings
Revises: 0024_assist_sessions
Create Date: 2026-10-10

One row per workspace that has made a choice. A workspace may hold one active key
per provider; this says which of them the assistant uses and with which model.
Without a row, the assistant uses the workspace's only (or newest) key and that
provider's suggested model.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_workspace_ai_settings"
down_revision: str | None = "0024_assist_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_ai_settings (
            workspace_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            updated_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workspace_ai_settings")
