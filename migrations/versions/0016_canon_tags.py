"""canon named tags

Revision ID: 0016_canon_tags
Revises: 0015_outbox_delivery
Create Date: 2026-10-08

Immutable named tags pointing to an exact root-branch revision, with replacement
history. Tag names are unique per (workspace, project, branch); moving a name to a
new revision records the previous pointer in canon_tag_history.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_canon_tags"
down_revision: str | None = "0015_outbox_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS canon_tags (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            name TEXT NOT NULL,
            revision INTEGER NOT NULL,
            commit_id TEXT NOT NULL,
            created_by_user_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, branch_id, name)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS canon_tags_commit_idx ON canon_tags (commit_id)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS canon_tag_history (
            id BIGSERIAL PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            name TEXT NOT NULL,
            revision INTEGER NOT NULL,
            commit_id TEXT NOT NULL,
            replaced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS canon_tag_history_name_idx "
        "ON canon_tag_history (workspace_id, project_id, branch_id, name, replaced_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS canon_tag_history")
    op.execute("DROP TABLE IF EXISTS canon_tags")
