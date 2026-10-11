"""story plan records: intent, kept apart from fact

Revision ID: 0028_plan_items
Revises: 0027_usage_allowance
Create Date: 2026-10-11

A story, its arcs, beats, scenes and threads. They are governed like the rest of
canon (reviewed operations, history, undo) but live in their own table: nothing
that reads entries, facts, beliefs or events can return a plan item, so what an
author intends is never mistaken for what is true in the story.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0028_plan_items"
down_revision: str | None = "0027_usage_allowance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_items (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('story', 'arc', 'beat', 'scene', 'thread')),
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            parent_id TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            sets_up JSONB NOT NULL DEFAULT '[]'::jsonb,
            pays_off JSONB NOT NULL DEFAULT '[]'::jsonb,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            provenance_id TEXT NOT NULL,
            created_revision BIGINT NOT NULL,
            retired_revision BIGINT,
            PRIMARY KEY (workspace_id, project_id, branch_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS plan_items_parent "
        "ON plan_items (workspace_id, project_id, parent_id, position)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plan_items")
