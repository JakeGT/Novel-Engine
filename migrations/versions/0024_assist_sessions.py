"""assistant conversations kept as evidence

Revision ID: 0024_assist_sessions
Revises: 0023_session_id_token
Create Date: 2026-10-10

One row per conversation with the assistant: what the author wrote, the questions
they were asked, their answers, and the proposal it led to. An open row can be
picked up again; a proposed row is the readable evidence behind a change. The id
is the same opaque session id that the proposal's operations cite as evidence.

The text here is the author's own writing about their story. It is tenant-scoped,
removed with its project or workspace, erased when the author puts an unfinished
conversation away, and never written to logs.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024_assist_sessions"
down_revision: str | None = "0023_session_id_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS assist_sessions (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('create', 'fill_in', 'change')),
            status TEXT NOT NULL CHECK (status IN ('open', 'proposed', 'closed')),
            description TEXT NOT NULL DEFAULT '',
            entity_type TEXT,
            name TEXT,
            entity_id TEXT,
            questions JSONB NOT NULL DEFAULT '[]'::jsonb,
            answers JSONB NOT NULL DEFAULT '[]'::jsonb,
            understood TEXT NOT NULL DEFAULT '',
            notes JSONB NOT NULL DEFAULT '[]'::jsonb,
            mutation_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS assist_sessions_open_idx "
        "ON assist_sessions (workspace_id, project_id, actor_user_id, status, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS assist_sessions_mutation_idx "
        "ON assist_sessions (workspace_id, project_id, mutation_id) "
        "WHERE mutation_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS assist_sessions")
