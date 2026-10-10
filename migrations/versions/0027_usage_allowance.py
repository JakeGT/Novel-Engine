"""a daily assistant allowance each workspace can set

Revision ID: 0027_usage_allowance
Revises: 0026_assist_suggestions
Create Date: 2026-10-10

How many tokens one person may spend through this workspace's key in a day. A
workspace without a row has only the site's own limit.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027_usage_allowance"
down_revision: str | None = "0026_assist_suggestions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_usage_allowance (
            workspace_id TEXT PRIMARY KEY,
            daily_tokens BIGINT NOT NULL CHECK (daily_tokens > 0),
            updated_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workspace_usage_allowance")
